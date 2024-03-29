import torch
import torch.nn as nn
from typing import List


# --------------------- Basic modules ---------------------
def get_conv2d(c1, c2, k, p, s, d, g, bias=False):
    conv = nn.Conv2d(c1, c2, k, stride=s, padding=p, dilation=d, groups=g, bias=bias)

    return conv

def get_activation(act_type=None):
    if act_type == 'relu':
        return nn.ReLU(inplace=True)
    elif act_type == 'lrelu':
        return nn.LeakyReLU(0.1, inplace=True)
    elif act_type == 'mish':
        return nn.Mish(inplace=True)
    elif act_type == 'silu':
        return nn.SiLU(inplace=True)
    elif act_type is None:
        return nn.Identity()
    else:
        raise NotImplementedError
        
def get_norm(norm_type, dim):
    if norm_type == 'BN':
        return nn.BatchNorm2d(dim)
    elif norm_type == 'GN':
        return nn.GroupNorm(num_groups=32, num_channels=dim)
    elif norm_type is None:
        return nn.Identity()
    else:
        raise NotImplementedError

class BasicConv(nn.Module):
    def __init__(self, 
                 in_dim,                   # in channels
                 out_dim,                  # out channels 
                 kernel_size=1,            # kernel size 
                 padding=0,                # padding
                 stride=1,                 # padding
                 dilation=1,               # dilation
                 act_type  :str = 'lrelu', # activation
                 norm_type :str = 'BN',    # normalization
                 depthwise :bool = False
                ):
        super(BasicConv, self).__init__()
        self.depthwise = depthwise
        if not depthwise:
            self.conv = get_conv2d(in_dim, out_dim, k=kernel_size, p=padding, s=stride, d=dilation, g=1)
            self.norm = get_norm(norm_type, out_dim)
        else:
            self.conv1 = get_conv2d(in_dim, in_dim, k=kernel_size, p=padding, s=stride, d=dilation, g=in_dim)
            self.norm1 = get_norm(norm_type, in_dim)
            self.conv2 = get_conv2d(in_dim, out_dim, k=1, p=0, s=1, d=1, g=1)
            self.norm2 = get_norm(norm_type, out_dim)
        self.act  = get_activation(act_type)

    def forward(self, x):
        if not self.depthwise:
            return self.act(self.norm(self.conv(x)))
        else:
            # Depthwise conv
            x = self.norm1(self.conv1(x))
            # Pointwise conv
            x = self.norm2(self.conv2(x))
            return x


# --------------------- Yolov8 modules ---------------------
class MDown(nn.Module):
    def __init__(self,
                 in_dim    :int,
                 out_dim   :int,
                 act_type  :str   = 'silu',
                 norm_type :str   = 'BN',
                 depthwise :bool  = False,
                 ) -> None:
        super().__init__()
        inter_dim = out_dim // 2
        self.downsample_1 = nn.Sequential(
            nn.MaxPool2d((2, 2), stride=2),
            BasicConv(in_dim, inter_dim, kernel_size=1, act_type=act_type, norm_type=norm_type)
        )
        self.downsample_2 = nn.Sequential(
            BasicConv(in_dim, inter_dim, kernel_size=1, act_type=act_type, norm_type=norm_type),
            BasicConv(inter_dim, inter_dim,
                      kernel_size=3, padding=1, stride=2,
                      act_type=act_type, norm_type=norm_type, depthwise=depthwise)
        )
        if in_dim == out_dim:
            self.output_proj = nn.Identity()
        else:
            self.output_proj = BasicConv(inter_dim * 2, out_dim, kernel_size=1, act_type=act_type, norm_type=norm_type)

    def forward(self, x):
        x1 = self.downsample_1(x)
        x2 = self.downsample_2(x)

        out = self.output_proj(torch.cat([x1, x2], dim=1))

        return out

class Bottleneck(nn.Module):
    def __init__(self,
                 in_dim      :int,
                 out_dim     :int,
                 kernel_size :List  = [1, 3],
                 expansion   :float = 0.5,
                 shortcut    :bool  = False,
                 act_type    :str   = 'silu',
                 norm_type   :str   = 'BN',
                 depthwise   :bool  = False,
                 ) -> None:
        super(Bottleneck, self).__init__()
        inter_dim = int(out_dim * expansion)
        # ----------------- Network setting -----------------
        self.conv_layer1 = BasicConv(in_dim, inter_dim,
                                     kernel_size=kernel_size[0], padding=kernel_size[0]//2, stride=1,
                                     act_type=act_type, norm_type=norm_type, depthwise=depthwise)
        self.conv_layer2 = BasicConv(inter_dim, out_dim,
                                     kernel_size=kernel_size[1], padding=kernel_size[1]//2, stride=1,
                                     act_type=act_type, norm_type=norm_type, depthwise=depthwise)
        self.shortcut = shortcut and in_dim == out_dim

    def forward(self, x):
        h = self.conv_layer2(self.conv_layer1(x))

        return x + h if self.shortcut else h

class ELANLayer(nn.Module):
    def __init__(self,
                 in_dim,
                 out_dim,
                 expansion  :float = 0.5,
                 num_blocks :int   = 1,
                 shortcut   :bool  = False,
                 act_type   :str   = 'silu',
                 norm_type  :str   = 'BN',
                 depthwise  :bool  = False,
                 ) -> None:
        super(ELANLayer, self).__init__()
        inter_dim = round(out_dim * expansion)
        self.input_proj  = BasicConv(in_dim, inter_dim * 2, kernel_size=1, act_type=act_type, norm_type=norm_type)
        self.output_proj = BasicConv((2 + num_blocks) * inter_dim, out_dim, kernel_size=1, act_type=act_type, norm_type=norm_type)
        self.module      = nn.ModuleList([Bottleneck(inter_dim,
                                                     inter_dim,
                                                     kernel_size = [3, 3],
                                                     expansion   = 1.0,
                                                     shortcut    = shortcut,
                                                     act_type    = act_type,
                                                     norm_type   = norm_type,
                                                     depthwise   = depthwise)
                                                     for _ in range(num_blocks)])

    def forward(self, x):
        # Input proj
        x1, x2 = torch.chunk(self.input_proj(x), 2, dim=1)
        out = list([x1, x2])

        # Bottlenecl
        out.extend(m(out[-1]) for m in self.module)

        # Output proj
        out = self.output_proj(torch.cat(out, dim=1))

        return out

class ELANLayerFPN(nn.Module):
    def __init__(self,
                 in_dim,
                 out_dim,
                 num_blocks :int   = 1,
                 expansion  :float = 0.5,
                 act_type   :str   = 'silu',
                 norm_type  :str   = 'BN',
                 depthwise  :bool  = False,
                 ) -> None:
        super(ELANLayerFPN, self).__init__()
        inter_dim_1 = round(out_dim * expansion)
        inter_dim_2 = round(inter_dim_1* expansion)
        # Branch-1
        self.branch_1 = BasicConv(in_dim, inter_dim_1, kernel_size=1, act_type=act_type, norm_type=norm_type)
        # Branch-2
        self.branch_2 = BasicConv(in_dim, inter_dim_1, kernel_size=1, act_type=act_type, norm_type=norm_type)
        # Branch-3
        branch_3 = []
        for i in range(num_blocks):
            if i == 0:
                branch_3.append(BasicConv(inter_dim_1, inter_dim_2, kernel_size=3, padding=1,
                                          act_type=act_type, norm_type=norm_type, depthwise=depthwise))
            else:
                branch_3.append(BasicConv(inter_dim_2, inter_dim_2, kernel_size=3, padding=1,
                                          act_type=act_type, norm_type=norm_type, depthwise=depthwise))
        self.branch_3 = nn.Sequential(*branch_3)
        # Branch-4
        self.branch_4 = nn.Sequential(*[BasicConv(inter_dim_2, inter_dim_2, kernel_size=3, padding=1,
                                                  act_type=act_type, norm_type=norm_type, depthwise=depthwise)
                                                     for _ in range(num_blocks)])
        # Branch-5
        self.branch_5 = nn.Sequential(*[BasicConv(inter_dim_2, inter_dim_2, kernel_size=3, padding=1,
                                                  act_type=act_type, norm_type=norm_type, depthwise=depthwise)
                                                     for _ in range(num_blocks)])
        # Branch-6
        self.branch_6 = nn.Sequential(*[BasicConv(inter_dim_2, inter_dim_2, kernel_size=3, padding=1,
                                                  act_type=act_type, norm_type=norm_type, depthwise=depthwise)
                                                     for _ in range(num_blocks)])
        self.output_proj = BasicConv(2*inter_dim_1 + 4*inter_dim_2, out_dim, kernel_size=1, act_type=act_type, norm_type=norm_type)

    def forward(self, x):
        # Elan
        x1 = self.branch_1(x)
        x2 = self.branch_2(x)
        x3 = self.branch_3(x2)
        x4 = self.branch_4(x3)
        x5 = self.branch_5(x4)
        x6 = self.branch_6(x5)

        # Output proj
        out = list([x1, x2, x3, x4, x5, x6])
        out = self.output_proj(torch.cat(out, dim=1))

        return out
