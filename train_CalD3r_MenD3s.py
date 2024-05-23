from datetime import datetime
from statistics import mean
from utils.logger import logger
import torch.nn.parallel
import torch.optim
import torch
from utils.loaders import CalD3R_MenD3s_Dataset
from utils.args import args
from utils.utils import pformat_dict
import utils
import numpy as np
import os
import models as model_list
import tasks
import wandb

# global variables among training functions
training_iterations = 0
modalities = None
np.random.seed(13696641)
torch.manual_seed(13696641)


def init_operations():
    """
    parse all the arguments, generate the logger, check gpus to be used and wandb
    """
    logger.info("Running with parameters: " + pformat_dict(args, indent=1))

    # this is needed for multi-GPUs systems where you just want to use a predefined set of GPUs
    if args.gpus is not None:
        logger.debug('Using only these GPUs: {}'.format(args.gpus))
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpus)

    # wanbd logging configuration
    if args.wandb_name is not None:
        wandb.init(group=args.wandb_name, dir=args.wandb_dir)
        wandb.run.name = args.name


def main():
    global training_iterations, modalities
    init_operations()
    modalities = args.modality

    # recover num_classes, valid paths, domains, 
    num_classes, valid_labels, source_domain, target_domain = utils.utils.get_domains_and_labels(args)
    
    # device where training is run
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    models = {}
    logger.info("Instantiating models per modality")
    for m in modalities:
        logger.info('{} Net\tModality: {}'.format(args.models[m].model, m))

        models[m] = getattr(model_list, args.models[m].model)()

    # The models (for each modality) are wrapped into the EmotionRecognition task which manages all the training-testing
    emotion_classifier = tasks.EmotionRecognition("emotion-classifier", 
                                                 models, 
                                                 args.batch_size,
                                                 args.total_batch, 
                                                 args.models_dir, 
                                                 num_classes,
                                                 args.train.num_clips, 
                                                 args.models, 
                                                 args=args)
    emotion_classifier.load_on_gpu(device)

    if args.action == "train":
        # resume_from argument is adopted in case of restoring from a checkpoint
        if args.resume_from is not None:
            emotion_classifier.load_last_model(args.resume_from)
        
        #* USE GRADIENT ACCUMULATION (the batches are devided into smaller batches with gradient accumulation)
        #* TOTAL_BATCH (128) -> 4* BATCH_SIZE (32)
        #* Every TOTAL_BATCH has to be iterated (forward pass + backward pass) "args.train.num_iter" times (5000)
        #* So, each BATCH_SIZE (32) needs to be passed  train.num_iter 
        training_iterations = args.train.num_iter * (args.total_batch // args.batch_size)
        
        
        #*All dataloaders are generated here
        train_loader = torch.utils.data.DataLoader(CalD3R_MenD3s_Dataset(modalities,
                                                                         'train', 
                                                                         args.dataset, 
                                                                         None),
                                                   batch_size=args.batch_size, 
                                                   shuffle=True,
                                                   num_workers=args.dataset.workers, 
                                                   pin_memory=True, 
                                                   drop_last=True)


        val_loader = torch.utils.data.DataLoader(CalD3R_MenD3s_Dataset(modalities,
                                                                       'val', 
                                                                        args.dataset,
                                                                        None),
                                                 batch_size=args.batch_size, 
                                                 shuffle=False,
                                                 num_workers=args.dataset.workers, 
                                                 pin_memory=True, 
                                                 drop_last=False)
        
        train(emotion_classifier, train_loader, val_loader, device, num_classes)

    #*test/validate
    elif args.action == "test":
        if args.resume_from is not None:
            emotion_classifier.load_last_model(args.resume_from)
            
        test_loader = torch.utils.data.DataLoader(CalD3R_MenD3s_Dataset(modalities,
                                                                       'val', 
                                                                        args.dataset,
                                                                        None),
                                                 batch_size=args.batch_size, 
                                                 shuffle=False,
                                                 num_workers=args.dataset.workers, 
                                                 pin_memory=True, 
                                                 drop_last=False)

        validate(emotion_classifier, test_loader, device, emotion_classifier.current_iter, num_classes)


def train(emotion_classifier, train_loader, val_loader, device, num_classes):
    """
    function to train 1 model for modality on the training set
    
    emotion_classifier: Task containing the model to be trained for each modality
    train_loader: dataloader containing the training data
    val_loader: dataloader containing the validation data
    device: device on which you want to test
    num_classes: int, number of classes in the classification problem
    """
    global training_iterations, modalities

    data_loader_source = iter(train_loader)
    emotion_classifier.train(True)
    emotion_classifier.zero_grad()
    
    #*current_iter is just for restoring from a saved run. Otherwise iteration is set to 0.
    iteration = emotion_classifier.current_iter * (args.total_batch // args.batch_size)

    #* real_iter is the number of iterations on TOTAL_BATCH
    #* this is needed because the lr schedule is defined on the real_iter
    for i in range(iteration, training_iterations):
        #* iteration in BATCH_SIZE < TOTAL_BATCH
        real_iter = (i + 1) / (args.total_batch // args.batch_size)
        if real_iter == args.train.lr_steps:
            # learning rate decay at iteration = lr_steps
            emotion_classifier.reduce_learning_rate()
        # gradient_accumulation_step is a bool used to understand if we accumulated at least total_batch samples' gradient
        gradient_accumulation_step = real_iter.is_integer()

        """
        Retrieve the data from the loaders
        """
        start_t = datetime.now()
        
        #* the following code is necessary as we do not reason in terms of epochs so,
        #* as soon as the dataloader is finished we need to redefine the iterator
        try:
            #source data is a dictionary: {"RGB": [[[batch_size (32)*num_clips (5)*1024]]], }
            #source_label is batchsize (32)
            source_data, source_label = next(data_loader_source) #*get the next batch of data with next()!
        except StopIteration:
            data_loader_source = iter(train_loader)
            source_data, source_label = next(data_loader_source)
        end_t = datetime.now()

        logger.info(f"Iteration {i}/{training_iterations} batch retrieved! Elapsed time = "
                    f"{(end_t - start_t).total_seconds() // 60} m {(end_t - start_t).total_seconds() % 60} s")


        #* emotion recognition
        source_label = source_label.to(device)
        data = {}
        for m in modalities:
            data[m] = source_data[m].to(device)
            #print("shape from the modalities for loop is:",data[m].shape)
        logits, _ = emotion_classifier.forward(data)
        emotion_classifier.compute_loss(logits, source_label, loss_weight=1)
        emotion_classifier.backward(retain_graph=False)
        emotion_classifier.compute_accuracy(logits, source_label)
                
        # update weights and zero gradients if total_batch samples are passed
        if gradient_accumulation_step:
            logger.info("[%d/%d]\tlast Verb loss: %.4f\tMean verb loss: %.4f\tAcc@1: %.2f%%\tAccMean@1: %.2f%%" %
                        (real_iter, args.train.num_iter, emotion_classifier.loss.val, emotion_classifier.loss.avg,
                         emotion_classifier.accuracy.val[1], emotion_classifier.accuracy.avg[1]))

            emotion_classifier.check_grad()
            emotion_classifier.step()
            emotion_classifier.zero_grad()

        # every eval_freq "real iteration" (iterations on total_batch) the validation is done
        if gradient_accumulation_step and real_iter % args.train.eval_freq == 0:
            val_metrics = validate(emotion_classifier, val_loader, device, int(real_iter), num_classes)

            if val_metrics['top1'] <= emotion_classifier.best_iter_score:
                logger.info("New best accuracy {:.2f}%"
                            .format(emotion_classifier.best_iter_score))
            else:
                logger.info("New best accuracy {:.2f}%".format(val_metrics['top1']))
                emotion_classifier.best_iter = real_iter
                emotion_classifier.best_iter_score = val_metrics['top1']

            emotion_classifier.save_model(real_iter, val_metrics['top1'], prefix=None)
            emotion_classifier.train(True)


def validate(model, val_loader, device, it, num_classes):
    """
    function to validate the model on the test set
    
    model: Task containing the model to be tested
    val_loader: dataloader containing the validation data
    device: device on which you want to test
    it: int, iteration among the training num_iter at which the model is tested
    num_classes: int, number of classes in the classification problem
    """
    global modalities

    model.reset_acc()
    model.train(False)
    logits = {}

    # Iterate over the models
    with torch.no_grad():
        for i_val, (data, label) in enumerate(val_loader):
            label = label.to(device)

            for m in modalities:
                batch = data[m].shape[0]
                logits[m] = torch.zeros((args.test.num_clips, batch, num_classes)).to(device)
            
            for m in modalities:
                data[m] = data[m].to(device)

            output, _ = model(data)
            for m in modalities:
                logits[m] = output[m]
            
            model.compute_accuracy(logits, label)

            if (i_val + 1) % (len(val_loader) // 5) == 0:
                logger.info("[{}/{}] top1= {:.3f}% top5 = {:.3f}%".format(i_val + 1, len(val_loader),
                                                                          model.accuracy.avg[1], model.accuracy.avg[5]))

        class_accuracies = [(x / y) * 100 for x, y in zip(model.accuracy.correct, model.accuracy.total)]
        logger.info('Final accuracy: top1 = %.2f%%\ttop5 = %.2f%%' % (model.accuracy.avg[1],
                                                                      model.accuracy.avg[5]))
        for i_class, class_acc in enumerate(class_accuracies):
            logger.info('Class %d = [%d/%d] = %.2f%%' % (i_class,
                                                         int(model.accuracy.correct[i_class]),
                                                         int(model.accuracy.total[i_class]),
                                                         class_acc))

    logger.info('Accuracy by averaging class accuracies (same weight for each class): {}%'
                .format(np.array(class_accuracies).mean(axis=0)))
    test_results = {'top1': model.accuracy.avg[1], 'top5': model.accuracy.avg[5],
                    'class_accuracies': np.array(class_accuracies)}

    with open(os.path.join(args.log_dir, f'val_precision_{args.dataset.shift.split("-")[0]}-'
                                         f'{args.dataset.shift.split("-")[-1]}.txt'), 'a+') as f:
        f.write("[%d/%d]\tAcc@top1: %.2f%%\n" % (it, args.train.num_iter, test_results['top1']))

    return test_results

if __name__ == '__main__':
    main()