import numpy as np
import megengine
import megengine.module as M
from megengine.module.conv import Conv2d, ConvTranspose2d
import megengine.functional as F
from edit.models.builder import BACKBONES


class HSA(M.Module):
    def __init__(self, K):
        super(HSA, self).__init__()
        self.K = K
        self.conv = M.Sequential(
            Conv2d(3, K**2, 3, 1, 1),
            M.ReLU()
        )   
        
    def forward(self, now_LR, pre_h_SD):
        """
        now_LR: B,3,H,W
        pre_h_SD: B,48,H,W
        """
        batch, C, H, W = pre_h_SD.shape
        kernels = self.conv(now_LR) # [B, k*k, H, W]
        batchwise_ans = []
        for idx in range(batch):
            kernel = kernels[idx]  # [k*k, H, W]
            kernel = F.dimshuffle(kernel, (1, 2, 0)) # [H, W , k*k]
            kernel = F.reshape(kernel, (H, W, 1, self.K, self.K, 1))
            kernel = F.broadcast_to(kernel, (C, H, W, 1, self.K, self.K, 1))
            batchwise_ans.append(F.local_conv2d(F.add_axis(pre_h_SD[idx], 0), kernel, [1, 1], [1, 1], [1, 1])) # [1, C, H, W]      some bug with padding       
        similarity_matrix = F.concat(batchwise_ans, axis=0) # [B,C,H,W]
        del batchwise_ans
        similarity_matrix = F.sigmoid(similarity_matrix)
        return F.multiply(pre_h_SD, similarity_matrix)

class SDBlock(M.Module):
    def __init__(self, channel_nums):
        super(SDBlock, self).__init__()
        self.netS = M.Sequential(
            Conv2d(channel_nums, channel_nums, 3, 1, 1),
            M.ReLU(),
            Conv2d(channel_nums, channel_nums, 3, 1, 1)
        )
        self.netD = M.Sequential(
            Conv2d(channel_nums, channel_nums, 3, 1, 1),
            M.ReLU(),
            Conv2d(channel_nums, channel_nums, 3, 1, 1)
        )

    def forward(self, S, D):
        SUM = self.netS(S) + self.netD(D)
        return S + SUM, D + SUM


@BACKBONES.register_module()
class RSDN(M.Module):
    """RSDN network structure.

    Paper:
    Ref repo:

    Args:
    """

    def __init__(self,
                 in_channels=3,
                 out_channels=3,
                 mid_channels = 128,
                 hidden_channels = 3 * 4 * 4,
                 blocknums = 5,
                 upscale_factor=4):
        super(RSDN, self).__init__()
        self.hsa = HSA(3)
        self.blocknums = blocknums
        self.hidden_channels = hidden_channels
        SDBlocks = []
        for _ in range(blocknums):
            SDBlocks.append(SDBlock(mid_channels))
        self.SDBlocks = M.Sequential(*SDBlocks)
        
        self.pre_SD_S = M.Sequential(
            Conv2d(2*(3 + hidden_channels), mid_channels, 3, 1, 1),
            M.ReLU(),
        )
        self.pre_SD_D = M.Sequential(
            Conv2d(2*(3 + hidden_channels), mid_channels, 3, 1, 1),
            M.ReLU(),
        )
        self.conv_SD = M.Sequential(
            Conv2d(mid_channels, hidden_channels, 3, 1, 1),
            M.ReLU(),
        )
        self.convS = Conv2d(mid_channels, hidden_channels, 3, 1, 1)
        self.convD = Conv2d(mid_channels, hidden_channels, 3, 1, 1)
        self.convHR = Conv2d(2 * hidden_channels, hidden_channels, 3, 1, 1)

        self.trans_S = ConvTranspose2d(hidden_channels, 3, 4, 4, 0, bias=False)
        self.trans_D = ConvTranspose2d(hidden_channels, 3, 4, 4, 0, bias=False)
        self.trans_HR = ConvTranspose2d(hidden_channels, 3, 4, 4, 0, bias=False)

    def forward(self, It, S, D, pre_S, pre_D, pre_S_hat=None, pre_D_hat=None, pre_SD=None):
        B, _, H, W = It.shape
        if pre_S_hat is None:
            assert pre_D_hat is None and pre_SD is None
            pre_S_hat = megengine.tensor(np.zeros((B, self.hidden_channels, H, W), dtype=np.float32))
            pre_D_hat = F.zeros_like(pre_S_hat)
            pre_SD = F.zeros_like(pre_S_hat)

        # pre_SD = self.hsa(It, pre_SD) # auto select
        S = F.concat([pre_S, S, pre_S_hat, pre_SD], axis = 1)
        S = self.pre_SD_S(S)
        D = F.concat([pre_D, D, pre_D_hat, pre_SD], axis = 1) 
        D = self.pre_SD_D(D)
        for i in range(self.blocknums):
            S,D = self.SDBlocks[i](S, D)
        pre_SD = self.conv_SD(S+D)
        S = self.convS(S)
        D = self.convD(D)
        I = self.convHR(F.concat([S, D], axis=1))
        return self.trans_HR(I), pre_SD, S, D, self.trans_S(S), self.trans_D(D)

    def init_weights(self, pretrained=None, strict=True):
        # 这里也可以进行参数的load，比如不在之前保存的路径中的模型（预训练好的）
        pass
        # """Init weights for models.
        #
        # Args:
        #     pretrained (str, optional): Path for pretrained weights. If given None, pretrained weights will not be loaded. Defaults to None.
        #     strict (boo, optional): Whether strictly load the pretrained model.
        #         Defaults to True.
        # """
        # if isinstance(pretrained, str):
        #     load_checkpoint(self, pretrained, strict=strict, logger=logger)
        # elif pretrained is None:
        #     pass  # use default initialization
        # else:
        #     raise TypeError('"pretrained" must be a str or None. '
        #                     f'But received {type(pretrained)}.')
