import argparse
import math
import os
import random

import numpy as np
import pandas as pd
import torch
from pytorchvideo.data import make_clip_sampler, labeled_video_dataset
from pytorchvideo.models import create_slowfast
from torch.backends import cudnn
from torch.nn import CrossEntropyLoss
from torch.optim import Adam, SGD
from torch.utils.data import DataLoader
from tqdm import tqdm

#from utils import train_transform, test_transform, clip_duration, num_classes

from pytorchvideo.transforms import ApplyTransformToKey, UniformTemporalSubsample, RandomShortSideScale, \
    ShortSideScale, Normalize
from torch import nn
from torchvision.transforms import Compose, Lambda, RandomCrop, RandomHorizontalFlip, CenterCrop

side_size = 392
max_size = 392
mean = [0.45, 0.45, 0.45]
std = [0.225, 0.225, 0.225]
crop_size = 392
num_frames = 32
sampling_rate = 1
frames_per_second = 32/6
clip_duration = (num_frames * sampling_rate) / frames_per_second
num_classes = 3
#checkpoint_path = '/kaggle/working/SLOWFAST_8x8_R50.pyth'

data_root = "/home/k/kai/data/all"
batch_size = 6
epochs = 50
save_root = '/home/k/kai/CheckPoints/Res_392_Batch_6_008'

# for reproducibility
random.seed(1)
np.random.seed(1)
torch.manual_seed(1)
cudnn.deterministic = True
cudnn.benchmark = True




class PackPathway(nn.Module):
    """
    Transform for converting video frames as a list of tensors.
    """

    def __init__(self, alpha=4):
        super().__init__()
        self.alpha = alpha

    def forward(self, frames):
        fast_pathway = frames
        # perform temporal sampling from the fast pathway.
        slow_pathway = torch.index_select(frames, 1,
                                          torch.linspace(0, frames.shape[1] - 1, frames.shape[1] // self.alpha).long())
        frame_list = [slow_pathway, fast_pathway]
        return frame_list


train_transform = ApplyTransformToKey(key="video", transform=Compose(
    [UniformTemporalSubsample(num_frames), Lambda(lambda x: x / 255.0), Normalize(mean, std), ShortSideScale(size=side_size), PackPathway()]))

test_transform = ApplyTransformToKey(key="video", transform=Compose(
    [UniformTemporalSubsample(num_frames), Lambda(lambda x: x / 255.0), Normalize(mean, std), ShortSideScale(size=side_size), PackPathway()]))



# train for one epoch
def train(model, data_loader, train_optimizer):
    model.train()
    total_loss, total_acc, total_num = 0.0, 0, 0
    train_bar = tqdm(data_loader, total=math.ceil(train_data.num_videos / batch_size), dynamic_ncols=True)
    for batch in train_bar:
        video, label = [i.cuda() for i in batch['video']], batch['label'].cuda()
        
        train_optimizer.zero_grad()
        pred = model(video)
        loss = loss_criterion(pred, label)
        total_loss += loss.item() * video[0].size(0)
        total_acc += (torch.eq(pred.argmax(dim=-1), label)).sum().item()
        loss.backward()
        train_optimizer.step()

        total_num += video[0].size(0)
        train_bar.set_description('Train Epoch: [{}/{}] Loss: {:.4f} Acc: {:.2f}%'
                                  .format(epoch, epochs, total_loss / total_num, total_acc * 100 / total_num))

    return total_loss / total_num, total_acc / total_num


# test for one epoch
def val(model, data_loader):
    model.eval()
    with torch.no_grad():
        total_top_1, total_top_5, total_num = 0, 0, 0
        test_bar = tqdm(data_loader, total=math.ceil(test_data.num_videos / batch_size), dynamic_ncols=True)
        for batch in test_bar:
            video, label = [i.cuda() for i in batch['video']], batch['label'].cuda()
            pred = model(video)
            total_top_1 += (torch.eq(pred.argmax(dim=-1), label)).sum().item()
            total_top_5 += torch.any(torch.eq(pred.topk(k=2, dim=-1).indices, label.unsqueeze(dim=-1)),
                                     dim=-1).sum().item()
            total_num += video[0].size(0)
            test_bar.set_description('Test Epoch: [{}/{}] | Top-1:{:.2f}% | Top-5:{:.2f}%'
                                     .format(epoch, epochs, total_top_1 * 100 / total_num,
                                             total_top_5 * 100 / total_num))
    return total_top_1 / total_num, total_top_5 / total_num

'''
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train Model')
    # common args
    parser.add_argument('--data_root', default='data', type=str, help='Datasets root path')
    parser.add_argument('--batch_size', default=8, type=int, help='Number of videos in each mini-batch')
    parser.add_argument('--epochs', default=10, type=int, help='Number of epochs over the model to train')
    parser.add_argument('--save_root', default='result', type=str, help='Result saved root path')
    parser.add_argument("--learning_rate", default=3e-2, type=float,help="The initial learning rate for SGD.")
    parser.add_argument("--weight_decay", default=0, type=float,help="Weight deay if we apply some.")
# args parse
args = parser.parse_args()
'''


# data prepare
train_data = labeled_video_dataset('{}/train'.format(data_root), make_clip_sampler('random', clip_duration),
                                   transform=train_transform, decode_audio=False)
test_data = labeled_video_dataset('{}/test'.format(data_root),
                                  make_clip_sampler('constant_clips_per_video', clip_duration, 1),
                                  transform=test_transform, decode_audio=False)
train_loader = DataLoader(train_data, batch_size=batch_size, num_workers=8)
test_loader = DataLoader(test_data, batch_size=batch_size, num_workers=8)


#------------------------------------------------------------------------------------------------------------

# model define, loss setup and optimizer config
#slow_fast = create_slowfast(model_num_class=num_classes).cuda()


slow_fast = torch.hub.load('facebookresearch/pytorchvideo:main', model='slowfast_r50', pretrained=True).cuda()

#slow_fast.load_state_dict(torch.load('/kaggle/working/CheckPoints/Batch_2_sgd_lr001/slow_fast.pth', 'cuda'))
slow_fast.blocks[6].proj = torch.nn.Linear(in_features=2304, out_features=3, bias=True).cuda()

#------------------------------------------------------------------------------------------------------------


loss_criterion = CrossEntropyLoss()
# optimizer = Adam(slow_fast.parameters(), lr=1e-1)
optimizer = SGD(slow_fast.parameters(), lr=0.008, momentum=0.9,weight_decay=0.0002)


# optimizer = SGD([{'params':slow_fast.parameters(),'lr':args.learning_rate},{'params':model.head.parameters(),'lr':args.learning_rate}],
#                 lr=args.learning_rate,momentum=0.9,weight_decay=args.weight_decay)

# optimizer = SGD([{'params':slow_fast.blocks[0:6].parameters(),'lr':0.0001},
#                  {'params':slow_fast.blocks[6].dropout.parameters(),'lr':0.0001},
#                  {'params':slow_fast.blocks[6].proj.parameters(),'lr':0.001},
#                  {'params':slow_fast.blocks[6].output_pool.parameters(),'lr':0.0001}], 
#                 lr=0.0001,momentum=0.9,weight_decay=0.0001)



#print(optimizer)

##---------------------------------------------------------------------------------------------------------
# training loop
results = {'loss': [], 'acc': [], 'top-1': [], 'top-5': []}
if not os.path.exists(save_root):
    os.makedirs(save_root)
best_acc = 0.0
for epoch in range(1, epochs + 1):
    train_loss, train_acc = train(slow_fast, train_loader, optimizer)
    results['loss'].append(train_loss)
    results['acc'].append(train_acc * 100)
    top_1, top_5 = val(slow_fast, test_loader)
    results['top-1'].append(top_1 * 100)
    results['top-5'].append(top_5 * 100)
    # save statistics
    data_frame = pd.DataFrame(data=results, index=range(1, epoch + 1))
    data_frame.to_csv('{}/metrics.csv'.format(save_root), index_label='epoch')

    if top_1 > best_acc:
        best_acc = top_1
        torch.save(slow_fast.state_dict(), '{}/slow_fast.pth'.format(save_root))
        
