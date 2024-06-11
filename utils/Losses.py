import torch
from torch import nn
import torch.nn.functional as F
import utils
from utils.args import args
import torchaudio.transforms as T
import models as model_list

#!!!FOCAL LOSS
class FocalLoss(nn.Module):
    def __init__(self, alpha=1, gamma=2, reduction='mean'):
        '''
         Args:
            alpha (float): Weighting factor for the rare class. Default is 1.
            gamma (float): Focusing parameter. Default is 2.
            reduction (str): Specifies the reduction to apply to the output: 'none' | 'mean' | 'sum'. Default is 'mean'.
        '''
        super(FocalLoss, self).__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.reduction = reduction

    def forward(self, inputs, targets):
        # Cross entropy loss
        BCE_loss = F.cross_entropy(inputs, targets, reduction='none')
        # The probability of the true class
        pt = torch.exp(-BCE_loss)
        # Focal loss calculation
        F_loss = self.alpha * (1 - pt) ** self.gamma * BCE_loss

        if self.reduction == 'mean':
            return F_loss.mean()
        elif self.reduction == 'sum':
            return F_loss.sum()
        else:
            return F_loss

class CenterLoss(nn.Module):
    def __init__(self, num_classes, feat_dim, device='cpu'):
        super(CenterLoss, self).__init__()
        self.num_classes = num_classes
        self.feat_dim = feat_dim
        self.device = device
        
        # Initialize the centers
        self.centers = nn.Parameter(torch.randn(num_classes, feat_dim).to(device))
    
    def forward(self, features, labels):
        # Get the centers corresponding to the labels
        batch_size = features.size(0)
        centers_batch = self.centers.index_select(0, labels)
        
        # Calculate the center loss
        center_loss = F.mse_loss(features, centers_batch)
        return center_loss
    
class CEL_CL_Loss(nn.Module):
    def __init__(self, num_classes, feat_dim, lambda_center=0.5, device='cpu'):
        super(CEL_CL_Loss, self).__init__()
        self.cross_entropy_loss = nn.CrossEntropyLoss()
        self.center_loss = CenterLoss(num_classes, feat_dim, device)
        self.lambda_center = lambda_center

    def forward(self, logits, features, labels):
        ce_loss = self.cross_entropy_loss(logits, labels)
        c_loss = self.center_loss(features, labels)
        total_loss = ce_loss + self.lambda_center * c_loss
        return total_loss