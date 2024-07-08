import torch
from torch import nn
import torch.nn.functional as F
from torchvision import models
from torchvision.models import ResNet18_Weights, ResNet50_Weights, EfficientNet_B0_Weights,EfficientNet_B3_Weights
from transformers import AutoImageProcessor, AutoModel, AutoModelForImageClassification

#!PRETRAINED RESNET-18
class DEPTH_ResNet18(nn.Module):
    def __init__(self):
        super(DEPTH_ResNet18, self).__init__()
        self.model = models.resnet18(weights=ResNet18_Weights.DEFAULT) 

    def forward(self, x):
        x = self.model.conv1(x)
        x = self.model.bn1(x) 
        x = self.model.relu(x)
        x = self.model.maxpool(x)
        
        #?4 stages
        x = self.model.layer1(x)
        x = self.model.layer2(x)
        x = self.model.layer3(x) 
        feat = self.model.layer4(x) #[batch_size, 512, 7, 7]
        
        return x, {'feat': feat}
    

#!PRETRAINED RESNET50
class DEPTH_ResNet50(nn.Module):
    def __init__(self):
        super(DEPTH_ResNet50, self).__init__()
        
        #?PRETRAINED FER2013 RESNET-50
        outer_model = AutoModelForImageClassification.from_pretrained("KhaldiAbderrhmane/resnet50-facial-emotion-recognition", trust_remote_code=True) 
        self.model = outer_model.resnet
        
        #?PRETRAINED IMAGENET RESNET-50
        #self.model = models.resnet50(weights=ResNet50_Weights.DEFAULT)  #download pretrained weights? ==True, ResNet18_Weights.DEFAULT TO GET THE MOST UPDATED WEIGHTS
        
        # check the weights
        # with open('IMAGENETresnet50_weights.txt', 'w') as f:
        #     for name, param in self.model.named_parameters():
        #         if param.requires_grad:
        #             f.write(f'Layer Name: {name}\n')
        #             f.write(f'Weights:\n{param.data}\n\n')

    def forward(self, x):        
        x = self.model.conv1(x)
        x = self.model.bn1(x) 
        x = self.model.relu(x)
        x = self.model.maxpool(x)
        
        #?4 stages
        x = self.model.layer1(x)
        x = self.model.layer2(x)
        x = self.model.layer3(x) 
        feat = self.model.layer4(x) #[batch_size, 2048, 7, 7]
        
        return x, {'feat': feat}

#!EFFICIENTNET
class DEPTH_efficientnet_b0(nn.Module):
    def __init__(self):
        super(DEPTH_efficientnet_b0, self).__init__()
        
        self.model = models.efficientnet_b0(weights=EfficientNet_B0_Weights.DEFAULT) 
        self.feature_extractor = nn.Sequential(*list(self.model.children())[:-2]) #remove [avgpool layer, fc layer]
        # with open('FULL_b0.txt', 'w') as f:
        #     f.write(str(self.model))
        # with open('FEAT_b0.txt', 'w') as f:
        #     f.write(str(self.feature_extractor))

    def forward(self, x):              
        feat = self.feature_extractor(x)
        return x, {'feat': feat}
    
    
class DEPTH_efficientnet_b3(nn.Module):
    def __init__(self):
        super(DEPTH_efficientnet_b3, self).__init__()
        
        self.model = models.efficientnet_b3(weights=EfficientNet_B3_Weights.DEFAULT) 
        self.feature_extractor = nn.Sequential(*list(self.model.children())[:-2]) #remove [avgpool layer, fc layer]

    def forward(self, x):
        feat = self.feature_extractor(x)
        return x, {'feat': feat}