import torch
import torch.nn as nn
import torch.utils.model_zoo as model_zoo
import torch.nn.functional as F
# from thop import profile
import numpy as np
def global_std_pool2d(x):
    """2D global standard variation pooling"""
    return torch.std(x.view(x.size()[0], x.size()[1], -1, 1),
                     dim=2, keepdim=True)


__all__ = ['ResNet', 'resnet18', 'resnet34', 'resnet50', 'resnet101',
           'resnet152', 'resnext50_32x4d', 'resnext101_32x8d',
           'wide_resnet50_2', 'wide_resnet101_2']


model_urls = {
    'resnet18': 'https://download.pytorch.org/models/resnet18-5c106cde.pth',
    'resnet34': 'https://download.pytorch.org/models/resnet34-333f7ec4.pth',
    'resnet50': 'https://download.pytorch.org/models/resnet50-19c8e357.pth',
    'resnet101': 'https://download.pytorch.org/models/resnet101-5d3b4d8f.pth',
    'resnet152': 'https://download.pytorch.org/models/resnet152-b121ed2d.pth',
    'resnext50_32x4d': 'https://download.pytorch.org/models/resnext50_32x4d-7cdf4587.pth',
    'resnext101_32x8d': 'https://download.pytorch.org/models/resnext101_32x8d-8ba56ff5.pth',
    'wide_resnet50_2': 'https://download.pytorch.org/models/wide_resnet50_2-95faca4d.pth',
    'wide_resnet101_2': 'https://download.pytorch.org/models/wide_resnet101_2-32ee1156.pth',
}



def conv3x3(in_planes, out_planes, stride=1, groups=1, dilation=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=dilation, groups=groups, bias=False, dilation=dilation)


def conv1x1(in_planes, out_planes, stride=1):
    """1x1 convolution"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)


# class SpatialAttention(nn.Module):
#     def __init__(self, kernel_size=7):
#         super(SpatialAttention, self).__init__()
 
#         assert kernel_size in (3, 7), 'kernel size must be 3 or 7'
#         padding = 3 if kernel_size == 7 else 1
 
#         self.conv1 = nn.Conv2d(2, 1, kernel_size, padding=padding, bias=False)
#         self.sigmoid = nn.Sigmoid()
 
#     def forward(self, x):
#         avg_out = torch.mean(x, dim=1, keepdim=True)
#         max_out, _ = torch.max(x, dim=1, keepdim=True)
#         x = torch.cat([avg_out, max_out], dim=1)
#         x = self.conv1(x)
#         return self.sigmoid(x)

# class ChannelAttention(nn.Module):
#     def __init__(self, in_planes, ratio=16):
#         super(ChannelAttention, self).__init__()
#         self.avg_pool = nn.AdaptiveAvgPool2d(1)
#         self.max_pool = nn.AdaptiveMaxPool2d(1)
 
#         self.fc1   = nn.Conv2d(in_planes, in_planes // 16, 1, bias=False)
#         self.relu1 = nn.ReLU()
#         self.fc2   = nn.Conv2d(in_planes // 16, in_planes, 1, bias=False)
 
#         self.sigmoid = nn.Sigmoid()
 
#     def forward(self, x):
#         avg_out = self.fc2(self.relu1(self.fc1(self.avg_pool(x))))
#         max_out = self.fc2(self.relu1(self.fc1(self.max_pool(x))))
#         out = avg_out + max_out
#         return self.sigmoid(out)

class BasicBlock(nn.Module):
    expansion = 1
    __constants__ = ['downsample']

    def __init__(self, inplanes, planes, stride=1, downsample=None, groups=1,
                 base_width=64, dilation=1, norm_layer=None):
        super(BasicBlock, self).__init__()
        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        if groups != 1 or base_width != 64:
            raise ValueError('BasicBlock only supports groups=1 and base_width=64')
        if dilation > 1:
            raise NotImplementedError("Dilation > 1 not supported in BasicBlock")
        # Both self.conv1 and self.downsample layers downsample the input when stride != 1
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = norm_layer(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = norm_layer(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out

class SELayer(nn.Module):
    def __init__(self, channel, reduction=16):
        super(SELayer, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)        #全局平均池化，输入BCHW -> 输出 B*C*1*1
        self.fc = nn.Sequential(
            nn.Linear(channel, channel // reduction, bias=False),   #可以看到channel得被reduction整除，否则可能出问题
            nn.ReLU(inplace=True),
            nn.Linear(channel // reduction, channel, bias=False),
            nn.Sigmoid()
        )
 
    def forward(self, mean_var,x_std):
        b, c, _= mean_var.size()
        y = self.avg_pool(mean_var).view(-1,b)     #得到B*C*1*1,然后转成B*C，才能送入到FC层中。
        y = self.fc(y).view(c, b, 1, 1)     #得到B*C的向量，C个值就表示C个通道的权重。把B*C变为B*C*1*1是为了与四维的x运算。
        return x_std * y.expand_as(x_std)           #先把B*C*1*1变成B*C*H*W大小，其中每个通道上的H*W个值都相等。*表示对应位置相乘。


class ANN(nn.Module):
    def __init__(self, input_size=3584, reduced_size=128, n_ANNlayers=1, dropout_p=0.5):
        super(ANN, self).__init__()
        self.n_ANNlayers = n_ANNlayers
        self.fc0 = nn.Linear(input_size, reduced_size,bias=False)  #
        self.dropout = nn.Dropout(p=dropout_p)  #
        self.fc = nn.Linear(reduced_size, 2048*2 + 1024*2 + 512*2)  #

    def forward(self, input):
        b, c, _,_= input.size()
        input=input.view(b,c)
        input = self.fc0(input)  # linear
        for i in range(self.n_ANNlayers):  # nonlinear
            input = self.fc(self.dropout(F.relu(input)))
        input.view(b,2048*2 + 1024*2 + 512*2,1,1)
        return input

class Bottleneck(nn.Module):
    expansion = 4
    __constants__ = ['downsample']

    def __init__(self, inplanes, planes, stride=1, downsample=None, groups=1,
                 base_width=64, dilation=1, norm_layer=None):
        super(Bottleneck, self).__init__()
        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        width = int(planes * (base_width / 64.)) * groups
        # Both self.conv2 and self.downsample layers downsample the input when stride != 1
        self.conv1 = conv1x1(inplanes, width)
        self.bn1 = norm_layer(width)
        self.conv2 = conv3x3(width, width, stride, groups, dilation)
        self.bn2 = norm_layer(width)
        self.conv3 = conv1x1(width, planes * self.expansion)
        self.bn3 = norm_layer(planes * self.expansion)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out
# 定义STAattention模块
class STAttention(nn.Module):
    def __init__(self, in_channels, reduction=4):
        super(STAttention, self).__init__()
        self.avg_pool_t = nn.AdaptiveAvgPool3d((None, 1, 1))
        self.fc_t = nn.Sequential(
            nn.Conv3d(in_channels, in_channels // reduction, kernel_size=1, bias=False),
            nn.ReLU(inplace=True),
            nn.Conv3d(in_channels // reduction, in_channels, kernel_size=1, bias=False),
            nn.Sigmoid()
        )

        self.avg_pool_s = nn.AdaptiveAvgPool3d((1, None, None))
        self.fc_s = nn.Sequential(
            nn.Conv3d(in_channels, in_channels // reduction, kernel_size=1, bias=False),
            nn.ReLU(inplace=True),
            nn.Conv3d(in_channels // reduction, in_channels, kernel_size=1, bias=False),
            nn.Sigmoid()
        )

    def forward(self, x):
        n, c, t= x.size()

        # Reshape the input tensor to [n, c, t, 1, 1]
        x = x.view(n, c, t, 1, 1)

        # Add a dummy spatial dimension
        x = x.expand(n, c, t, 2, 2)

        # Temporal attention
        y = self.avg_pool_t(x)
        y = self.fc_t(y)
        x = x * y

        # Spatial attention
        y = self.avg_pool_s(x)
        y = self.fc_s(y)
        x = x * y

        # Remove the dummy spatial dimension
        x = x[:, :, :, 0, 0]

        return x
# 定义Transformer模块
class TransformerBlock(nn.Module):
    def __init__(self, d_model, num_heads, dim_feedforward):
        super(TransformerBlock, self).__init__()

        self.self_attn = nn.MultiheadAttention(d_model, num_heads)
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.linear2 = nn.Linear(dim_feedforward, d_model)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout1 = nn.Dropout(0.1)
        self.dropout2 = nn.Dropout(0.1)

    def forward(self, x):
        # 自注意力层
        attn_output, _ = self.self_attn(x, x, x)
        x = x + self.dropout1(attn_output)
        x = self.norm1(x)

        # 前馈网络层
        linear_output = self.linear2(self.dropout1(nn.functional.relu(self.linear1(x))))
        x = x + self.dropout2(linear_output)
        x = self.norm2(x)

        return x

class ResNet(nn.Module):

    def __init__(self, block, layers, num_classes=1000, zero_init_residual=False,
                 groups=1, width_per_group=64, replace_stride_with_dilation=None,
                 norm_layer=None):
        super(ResNet, self).__init__()
        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        self._norm_layer = norm_layer

        self.inplanes = 64
        self.dilation = 1
        if replace_stride_with_dilation is None:
            # each element in the tuple indicates if we should replace
            # the 2x2 stride with a dilated convolution instead
            replace_stride_with_dilation = [False, False, False]
        if len(replace_stride_with_dilation) != 3:
            raise ValueError("replace_stride_with_dilation should be None "
                             "or a 3-element tuple, got {}".format(replace_stride_with_dilation))
        self.groups = groups
        self.base_width = width_per_group
        self.conv1 = nn.Conv2d(3, self.inplanes, kernel_size=7, stride=2, padding=3,
                               bias=False)
        self.bn1 = norm_layer(self.inplanes)
        self.relu = nn.ReLU(inplace=True)
        # # 网络的第一层加入注意力机制
        # self.ca = ChannelAttention(self.inplanes)
        # self.sa = SpatialAttention()
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2,
                                       dilate=replace_stride_with_dilation[0])
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2,
                                       dilate=replace_stride_with_dilation[1])
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2,
                                       dilate=replace_stride_with_dilation[2])
        
        # STA注意力机制

        self.sta = STAttention(in_channels=8)


        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.CA  = SELayer(3584, reduction=16)
        self.ann = ANN(3584, 128, 1)
        # three stage spatial features (avg + std) + motion
        self.quality = self.quality_regression(4096+2048+1024+2048+256, 128,1)
        self.motion_CNN=nn.Sequential(
            nn.Linear(2304, 256),
            nn.Linear(256, 2304),          
        )
        self.rnn = nn.GRU(2304, 2304, batch_first=True)
        self.transformer_block = TransformerBlock(2304,8, 1024)


        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        if zero_init_residual:
            for m in self.modules():
                if isinstance(m, Bottleneck):
                    nn.init.constant_(m.bn3.weight, 0)
                elif isinstance(m, BasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)

    def _make_layer(self, block, planes, blocks, stride=1, dilate=False):
        norm_layer = self._norm_layer
        downsample = None
        previous_dilation = self.dilation
        if dilate:
            self.dilation *= stride
            stride = 1
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                norm_layer(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample, self.groups,
                            self.base_width, previous_dilation, norm_layer))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes, groups=self.groups,
                                base_width=self.base_width, dilation=self.dilation,
                                norm_layer=norm_layer))

        return nn.Sequential(*layers)

    def quality_regression(self,in_channels, middle_channels, out_channels):
        regression_block = nn.Sequential(
            nn.Linear(in_channels, middle_channels),
            nn.Linear(middle_channels, out_channels),          
        )

        return regression_block


    def _forward_impl(self, x, x_3D_features):
        # See note [TorchScript super()]
        # input dimension: batch x frames x 3 x height x width
        x_size = x.shape
        # x_3D: batch x frames x (2048 + 256)
        x_3D_features_size = x_3D_features.shape

        # x_3D_features_rnn=self.rnn(x_3D_features)
        # x_3D_features=x_3D_features_rnn[0]

        # x_3D_features_test=x_3D_features[:, -1, :]
        # x: batch * frames x 3 x height x width
        x = x.view(-1, x_size[2], x_size[3], x_size[4])

        # print(x_3D_features.size())
        # Add STAttention module here
        # x_3D_features = self.sta(x_3D_features)  
        # print(x_3D_features.size())
        # x_3D: batch * frames x (2048 + 256)
        x_3D_features = x_3D_features.view(-1, x_3D_features_size[2])
        # Transformer
        # x_3D_features = self.transformer_block(x_3D_features)

        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)

        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x_avg2 = self.avgpool(x)
        x_std2 = global_std_pool2d(x)

        x = self.layer3(x)
        x_avg3 = self.avgpool(x)
        x_std3 = global_std_pool2d(x)

        x = self.layer4(x)

        x_avg4 = self.avgpool(x)
        x_std4 = global_std_pool2d(x)
        # Attention Module
        x_MP=torch.cat((x_std2, x_std3,  x_std4), dim = 1)
        x_SP=torch.cat((x_avg2, x_avg3,  x_avg4), dim = 1)
        mean_var  = torch.std(x_MP,0)

        # 直接拼接
        # x = torch.cat((x_avg2, x_std2, x_avg3, x_std3, x_avg4, x_std4), dim = 1)  
        # x = torch.cat((x_avg2, x_avg3, x_avg4, x_std2, x_std3, x_std4), dim = 1)
        x_att = self.CA(mean_var,x_SP)
        x = self.ann(x_att)
        # x: batch * frames x (2048*2 + 1024*2 + 512*2)
        x = torch.flatten(x, 1)
        # x: batch * frames x (2048*2 + 1024*2 + 512*2 + 2048 + 512)
        x = torch.cat((x, x_3D_features), dim = 1)
        # x: batch * frames x 1
        # quality
        x = self.quality(x)
        # x: batch x frames
        x = x.view(x_size[0],x_size[1])
        # x: batch x 1
        x = torch.mean(x, dim = 1)
        
        return x

    def forward(self, x, x_3D_features):
        return self._forward_impl(x, x_3D_features)


def _resnet(arch, block, layers, pretrained, progress, **kwargs):
    model = ResNet(block, layers, **kwargs)
    if pretrained:
        state_dict = load_state_dict_from_url(model_urls[arch],
                                              progress=progress)
        model.load_state_dict(state_dict)
    return model


def resnet18(pretrained=False, progress=True, **kwargs):
    r"""ResNet-18 model from
    `"Deep Residual Learning for Image Recognition" <https://arxiv.org/pdf/1512.03385.pdf>`_
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    return _resnet('resnet18', BasicBlock, [2, 2, 2, 2], pretrained, progress,
                   **kwargs)


def resnet34(pretrained=False, progress=True, **kwargs):
    r"""ResNet-34 model from
    `"Deep Residual Learning for Image Recognition" <https://arxiv.org/pdf/1512.03385.pdf>`_
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    model = ResNet(BasicBlock, [3, 4, 6, 3], **kwargs)
    if pretrained:
        # model.load_state_dict(model_zoo.load_url(model_urls['resnet50']))
        model_dict = model.state_dict()
        pre_train_model = model_zoo.load_url(model_urls['resnet34'])
        pre_train_model = {k:v for k,v in pre_train_model.items() if k in model_dict}
        model_dict.update(pre_train_model)
        model.load_state_dict(model_dict)
    return model


def resnet50(pretrained=False, progress=True, **kwargs):
    r"""ResNet-50 model from
    `"Deep Residual Learning for Image Recognition" <https://arxiv.org/pdf/1512.03385.pdf>`_
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    model = ResNet(Bottleneck, [3, 4, 6, 3], **kwargs)
    # input = torch.randn(1, 3, 224, 224)
    # flops, params = profile(model, inputs=(input, ))
    # print('The flops is {:.4f}, and the params is {:.4f}'.format(flops/10e9, params/10e6))
    if pretrained:
        # model.load_state_dict(model_zoo.load_url(model_urls['resnet50']))
        model_dict = model.state_dict()
        pre_train_model = model_zoo.load_url(model_urls['resnet50'])
        pre_train_model = {k:v for k,v in pre_train_model.items() if k in model_dict}
        model_dict.update(pre_train_model)
        model.load_state_dict(model_dict)
    return model


def resnet101(pretrained=False, progress=True, **kwargs):
    r"""ResNet-101 model from
    `"Deep Residual Learning for Image Recognition" <https://arxiv.org/pdf/1512.03385.pdf>`_
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    # return _resnet('resnet101', Bottleneck, [3, 4, 23, 3], pretrained, progress,
    #                **kwargs)
    model = ResNet(Bottleneck, [3, 4, 23, 3], **kwargs)
    if pretrained:
        model_dict = model.state_dict()
        pre_train_model = model_zoo.load_url(model_urls['resnet101'])
        pre_train_model = {k:v for k,v in pre_train_model.items() if k in model_dict}
        model_dict.update(pre_train_model)
        model.load_state_dict(model_dict)
    return model


def resnet152(pretrained=False, progress=True, **kwargs):
    r"""ResNet-152 model from
    `"Deep Residual Learning for Image Recognition" <https://arxiv.org/pdf/1512.03385.pdf>`_
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    # return _resnet('resnet152', Bottleneck, [3, 8, 36, 3], pretrained, progress,
    #                **kwargs)
    model = ResNet(Bottleneck, [3, 8, 36, 3], **kwargs)
    if pretrained:
        model_dict = model.state_dict()
        pre_train_model = model_zoo.load_url(model_urls['resnet152'])
        pre_train_model = {k:v for k,v in pre_train_model.items() if k in model_dict}
        model_dict.update(pre_train_model)
        model.load_state_dict(model_dict)
    return model


def resnext50_32x4d(pretrained=False, progress=True, **kwargs):
    r"""ResNeXt-50 32x4d model from
    `"Aggregated Residual Transformation for Deep Neural Networks" <https://arxiv.org/pdf/1611.05431.pdf>`_
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    kwargs['groups'] = 32
    kwargs['width_per_group'] = 4
    #return _resnet('resnext50_32x4d', Bottleneck, [3, 4, 6, 3],
       #            pretrained, progress, **kwargs)
    model = ResNet(Bottleneck, [3, 4, 6, 3],
                   pretrained, progress, **kwargs)
    if pretrained:
        model_dict = model.state_dict()
        pre_train_model = model_zoo.load_url(model_urls['resnext50_32x4d'])
        pre_train_model = {k:v for k,v in pre_train_model.items() if k in model_dict}
        model_dict.update(pre_train_model)
        model.load_state_dict(model_dict)
    return model


def resnext101_32x8d(pretrained=False, progress=True, **kwargs):
    r"""ResNeXt-101 32x8d model from
    `"Aggregated Residual Transformation for Deep Neural Networks" <https://arxiv.org/pdf/1611.05431.pdf>`_
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    kwargs['groups'] = 32
    kwargs['width_per_group'] = 8
    # return _resnet('resnext101_32x8d', Bottleneck, [3, 4, 23, 3],
    #                pretrained, progress, **kwargs)
    model = ResNet(Bottleneck, [3, 4, 23, 3],
                   pretrained, progress, **kwargs)
    if pretrained:
        model_dict = model.state_dict()
        pre_train_model = model_zoo.load_url(model_urls['resnext101_32x8d'])
        pre_train_model = {k:v for k,v in pre_train_model.items() if k in model_dict}
        model_dict.update(pre_train_model)
        model.load_state_dict(model_dict)
    return model


def wide_resnet50_2(pretrained=False, progress=True, **kwargs):
    r"""Wide ResNet-50-2 model from
    `"Wide Residual Networks" <https://arxiv.org/pdf/1605.07146.pdf>`_
    The model is the same as ResNet except for the bottleneck number of channels
    which is twice larger in every block. The number of channels in outer 1x1
    convolutions is the same, e.g. last block in ResNet-50 has 2048-512-2048
    channels, and in Wide ResNet-50-2 has 2048-1024-2048.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    kwargs['width_per_group'] = 64 * 2
    return _resnet('wide_resnet50_2', Bottleneck, [3, 4, 6, 3],
                   pretrained, progress, **kwargs)


def wide_resnet101_2(pretrained=False, progress=True, **kwargs):
    r"""Wide ResNet-101-2 model from
    `"Wide Residual Networks" <https://arxiv.org/pdf/1605.07146.pdf>`_
    The model is the same as ResNet except for the bottleneck number of channels
    which is twice larger in every block. The number of channels in outer 1x1
    convolutions is the same, e.g. last block in ResNet-50 has 2048-512-2048
    channels, and in Wide ResNet-50-2 has 2048-1024-2048.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    kwargs['width_per_group'] = 64 * 2
    return _resnet('wide_resnet101_2', Bottleneck, [3, 4, 23, 3],
                   pretrained, progress, **kwargs)




if __name__ == "__main__":

    # device = "cuda" if torch.cuda.is_available() else "cpu"
    device = "cpu"
    model = resnet50(pretrained=False).to(device)
    # print(model)
    from thop import profile
    from thop import clever_format

    input = torch.randn(8,8,3,448,448)
    input_3D = torch.randn(8, 8, 2048+256)
    flops, params = profile(model, inputs=(input,input_3D,))
    flops, params = clever_format([flops, params], "%.3f")

    print(flops)
    print(params)
