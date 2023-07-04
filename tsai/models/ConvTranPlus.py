# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/081_models.ConvTranPlus.ipynb.

# %% auto 0
__all__ = ['ConvTran', 'tAPE', 'AbsolutePositionalEncoding', 'LearnablePositionalEncoding', 'Attention', 'Attention_Rel_Scl',
           'Attention_Rel_Vec', 'ConvTranBackbone', 'ConvTranPlus']

# %% ../../nbs/081_models.ConvTranPlus.ipynb 3
from collections import OrderedDict
from typing import Any

import math
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .layers import lin_nd_head

# %% ../../nbs/081_models.ConvTranPlus.ipynb 4
class tAPE(nn.Module):
    "time Absolute Position Encoding"

    def __init__(self, 
        d_model:int, # the embedding dimension
        seq_len=1024, # the max. length of the incoming sequence
        dropout:float=0.1, # dropout value
        scale_factor=1.0
        ):
        super().__init__()
        
        pe = torch.zeros(seq_len, d_model)  # positional encoding
        position = torch.arange(0, seq_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))

        pe[:, 0::2] = torch.sin((position * div_term)*(d_model/seq_len))
        pe[:, 1::2] = torch.cos((position * div_term)*(d_model/seq_len))
        pe = scale_factor * pe.unsqueeze(0)
        self.register_buffer('pe', pe)  # this stores the variable in the state_dict (used for non-trainable variables)

        self.dropout = nn.Dropout(p=dropout)

    def forward(self, x): # [batch size, sequence length, embed dim]
        x = x + self.pe
        return self.dropout(x)

# %% ../../nbs/081_models.ConvTranPlus.ipynb 6
class AbsolutePositionalEncoding(nn.Module):
    "Absolute positional encoding"

    def __init__(self, 
        d_model:int, # the embedding dimension
        seq_len=1024, # the max. length of the incoming sequence
        dropout:float=0.1, # dropout value
        scale_factor=1.0
        ):
        super().__init__()

        
        pe = torch.zeros(seq_len, d_model)  # positional encoding
        position = torch.arange(0, seq_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))

        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = scale_factor * pe.unsqueeze(0)
        self.register_buffer('pe', pe)  # this stores the variable in the state_dict (used for non-trainable variables)

        self.dropout = nn.Dropout(p=dropout)

    def forward(self, x): # [batch size, sequence length, embed dim]
        x = x + self.pe
        return self.dropout(x)

# %% ../../nbs/081_models.ConvTranPlus.ipynb 8
class LearnablePositionalEncoding(nn.Module):
    "Learnable positional encoding"


    def __init__(self, 
        d_model:int, # the embedding dimension
        seq_len=1024, # the max. length of the incoming sequence
        dropout:float=0.1, # dropout value
        ):
        super().__init__()

        self.pe = nn.Parameter(torch.empty(seq_len, d_model))  # requires_grad automatically set to True
        nn.init.uniform_(self.pe, -0.02, 0.02)

        self.dropout = nn.Dropout(p=dropout)

    def forward(self, x): #[batch size, seq_len, embed dim]
        x = x + self.pe
        return self.dropout(x)

# %% ../../nbs/081_models.ConvTranPlus.ipynb 10
class Attention(nn.Module):
    def __init__(self, 
        emb_size:int, # Embedding dimension
        num_heads:int=8, # number of attention heads
        dropout:float=0.01, # dropout
        ):
        super().__init__()
        self.num_heads = num_heads
        self.scale = emb_size ** -0.5
        self.key = nn.Linear(emb_size, emb_size, bias=False)
        self.value = nn.Linear(emb_size, emb_size, bias=False)
        self.query = nn.Linear(emb_size, emb_size, bias=False)

        self.dropout = nn.Dropout(dropout)
        self.to_out = nn.LayerNorm(emb_size)

    def forward(self, x): #[batch size, seq_len, embed dim]

        batch_size, seq_len, _ = x.shape
        k = self.key(x).reshape(batch_size, seq_len, self.num_heads, -1).permute(0, 2, 3, 1)
        v = self.value(x).reshape(batch_size, seq_len, self.num_heads, -1).transpose(1, 2)
        q = self.query(x).reshape(batch_size, seq_len, self.num_heads, -1).transpose(1, 2)

        attn = torch.matmul(q, k) * self.scale
        attn = nn.functional.softmax(attn, dim=-1)

        out = torch.matmul(attn, v) # [batch_size, num_heads, seq_len, d_head]
        out = out.transpose(1, 2) # [batch_size, seq_len, num_heads, d_head]
        out = out.reshape(batch_size, seq_len, -1) # [batch_size, seq_len, d_model]
        out = self.to_out(out)
        return out

# %% ../../nbs/081_models.ConvTranPlus.ipynb 12
class Attention_Rel_Scl(nn.Module):
    def __init__(self, 
        emb_size:int, # Embedding dimension
        seq_len:int, # sequence length
        num_heads:int=8, # number of attention heads
        dropout:float=0.01, # dropout
        ):
        super().__init__()

        self.seq_len = seq_len
        self.num_heads = num_heads
        self.scale = emb_size ** -0.5

        self.key = nn.Linear(emb_size, emb_size, bias=False)
        self.value = nn.Linear(emb_size, emb_size, bias=False)
        self.query = nn.Linear(emb_size, emb_size, bias=False)

        self.relative_bias_table = nn.Parameter(torch.zeros((2 * self.seq_len - 1), num_heads))
        coords = torch.meshgrid((torch.arange(1), torch.arange(self.seq_len)), indexing="xy")
        coords = torch.flatten(torch.stack(coords), 1)
        relative_coords = coords[:, :, None] - coords[:, None, :]
        relative_coords[1] += self.seq_len - 1
        relative_coords = relative_coords.permute(1, 2, 0)
        relative_index = relative_coords.sum(-1).flatten().unsqueeze(1)
        self.register_buffer("relative_index", relative_index)

        self.dropout = nn.Dropout(dropout)
        self.to_out = nn.LayerNorm(emb_size)

    def forward(self, x): #[batch size, seq_len, embed dim]
        batch_size, seq_len, _ = x.shape
        k = self.key(x).reshape(batch_size, seq_len, self.num_heads, -1).permute(0, 2, 3, 1)
        v = self.value(x).reshape(batch_size, seq_len, self.num_heads, -1).transpose(1, 2)
        q = self.query(x).reshape(batch_size, seq_len, self.num_heads, -1).transpose(1, 2)

        attn = torch.matmul(q, k) * self.scale # [seq_len, seq_len]
        attn = nn.functional.softmax(attn, dim=-1)

        relative_bias = self.relative_bias_table.gather(0, self.relative_index.repeat(1, 8))
        relative_bias = relative_bias.reshape(self.seq_len, self.seq_len, -1).permute(2, 0, 1).unsqueeze(0)
        attn = attn + relative_bias

        out = torch.matmul(attn, v) # [batch_size, num_heads, seq_len, d_head]
        out = out.transpose(1, 2) # [batch_size, seq_len, num_heads, d_head]
        out = out.reshape(batch_size, seq_len, -1) # [batch_size, seq_len, d_model]
        out = self.to_out(out)
        return out

# %% ../../nbs/081_models.ConvTranPlus.ipynb 14
class Attention_Rel_Vec(nn.Module):
    def __init__(self, 
        emb_size:int, # Embedding dimension
        seq_len:int, # sequence length
        num_heads:int=8, # number of attention heads
        dropout:float=0.01, # dropout
        ):
        super().__init__()

        self.seq_len = seq_len
        self.num_heads = num_heads
        self.scale = emb_size ** -0.5

        self.key = nn.Linear(emb_size, emb_size, bias=False)
        self.value = nn.Linear(emb_size, emb_size, bias=False)
        self.query = nn.Linear(emb_size, emb_size, bias=False)

        self.Er = nn.Parameter(torch.randn(self.seq_len, int(emb_size/num_heads)))

        self.register_buffer(
            "mask",
            torch.tril(torch.ones(self.seq_len, self.seq_len))
            .unsqueeze(0).unsqueeze(0)
        )

        self.dropout = nn.Dropout(dropout)
        self.to_out = nn.LayerNorm(emb_size)

    def forward(self, x): #[batch size, seq_len, embed dim]
        batch_size, seq_len, _ = x.shape
        k = self.key(x).reshape(batch_size, seq_len, self.num_heads, -1).permute(0, 2, 3, 1) # [batch_size, num_heads, seq_len, d_head]
        v = self.value(x).reshape(batch_size, seq_len, self.num_heads, -1).transpose(1, 2) # [batch_size, num_heads, seq_len, d_head]
        q = self.query(x).reshape(batch_size, seq_len, self.num_heads, -1).transpose(1, 2) # [batch_size, num_heads, seq_len, d_head]

        QEr = torch.matmul(q, self.Er.transpose(0, 1))
        Srel = self.skew(QEr) # [batch_size, self.num_heads, seq_len, seq_len]

        attn = torch.matmul(q, k) # [seq_len, seq_len]
        attn = (attn + Srel) * self.scale

        attn = nn.functional.softmax(attn, dim=-1)
        out = torch.matmul(attn, v) # [batch_size, num_heads, seq_len, d_head]
        out = out.transpose(1, 2) # [batch_size, seq_len, num_heads, d_head]
        out = out.reshape(batch_size, seq_len, -1) # [batch_size, seq_len, d_model]
        out = self.to_out(out)
        return out

    def skew(self, QEr): # [batch_size, num_heads, seq_len, seq_len]
        padded = nn.functional.pad(QEr, (1, 0)) # [batch_size, num_heads, seq_len, 1 + seq_len]
        batch_size, num_heads, num_rows, num_cols = padded.shape
        reshaped = padded.reshape(batch_size, num_heads, num_cols, num_rows) # [batch_size, num_heads, 1 + seq_len, seq_len]
        Srel = reshaped[:, :, 1:, :] # [batch_size, num_heads, seq_len, seq_len]
        return Srel

# %% ../../nbs/081_models.ConvTranPlus.ipynb 16
class ConvTranBackbone(nn.Module):
    def __init__(self, 
        c_in:int, 
        seq_len:int, 
        emb_size=16, # Internal dimension of transformer embeddings
        num_heads:int=8, # Number of multi-headed attention heads
        dim_ff:int=256, # Dimension of dense feedforward part of transformer layer
        abs_pos_encode:str='tAPE', # Absolute Position Embedding. choices={'tAPE', 'sin', 'learned', None}
        rel_pos_encode:str='eRPE', # Relative Position Embedding. choices={'eRPE', 'vector', None}
        dropout:float=0.01, # Droupout regularization ratio
        ):
        super().__init__()


        self.embed_layer = nn.Sequential(nn.Conv2d(1, emb_size*4, kernel_size=[1, 7], padding='same'), nn.BatchNorm2d(emb_size*4), nn.GELU())
        self.embed_layer2 = nn.Sequential(nn.Conv2d(emb_size*4, emb_size, kernel_size=[c_in, 1], padding='valid'), nn.BatchNorm2d(emb_size), nn.GELU())

        assert abs_pos_encode in ['tAPE', 'sin', 'learned', None]
        if abs_pos_encode == 'tAPE':
            self.abs_position = tAPE(emb_size, dropout=dropout, seq_len=seq_len)
        elif abs_pos_encode == 'sin':
            self.abs_position = AbsolutePositionalEncoding(emb_size, dropout=dropout, seq_len=seq_len)
        elif abs_pos_encode== 'learned':
            self.abs_position = LearnablePositionalEncoding(emb_size, dropout=dropout, seq_len=seq_len)
        else:
            self.abs_position = nn.Identity()

        assert rel_pos_encode in ['eRPE', 'vector', None]
        if rel_pos_encode == 'eRPE':
            self.attention_layer = Attention_Rel_Scl(emb_size, seq_len, num_heads=num_heads, dropout=dropout)
        elif rel_pos_encode == 'vector':
            self.attention_layer = Attention_Rel_Vec(emb_size, seq_len, num_heads=num_heads, dropout=dropout)
        else:
            self.attention_layer = Attention(emb_size, num_heads=num_heads, dropout=dropout)

        self.LayerNorm = nn.LayerNorm(emb_size, eps=1e-5)
        self.LayerNorm2 = nn.LayerNorm(emb_size, eps=1e-5)

        self.FeedForward = nn.Sequential(
            nn.Linear(emb_size, dim_ff),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(dim_ff, emb_size),
            nn.Dropout(dropout))

    def forward(self, x): # [batch size, c_in, seq_len]
        x = x.unsqueeze(1)
        x_src = self.embed_layer(x)
        x_src = self.embed_layer2(x_src).squeeze(2)
        x_src = x_src.permute(0, 2, 1)
        x_src_pos = self.abs_position(x_src)
        att = x_src + self.attention_layer(x_src_pos)
        att = self.LayerNorm(att)
        out = att + self.FeedForward(att)
        out = self.LayerNorm2(out)
        out = out.permute(0, 2, 1)
        return out


# %% ../../nbs/081_models.ConvTranPlus.ipynb 18
class ConvTranPlus(nn.Sequential):
    def __init__(self, 
        c_in:int, # Number of channels in input
        c_out:int, # Number of channels in output
        seq_len:int, # Number of input sequence length
        d:tuple=None,  # output shape (excluding batch dimension).
        emb_size=16, # Internal dimension of transformer embeddings
        num_heads:int=8, # Number of multi-headed attention heads
        dim_ff:int=256, # Dimension of dense feedforward part of transformer layer
        abs_pos_encode:str='tAPE', # Absolute Position Embedding. choices={'tAPE', 'sin', 'learned', None}
        rel_pos_encode:str='eRPE', # Relative Position Embedding. choices={'eRPE', 'vector', None}
        encoder_dropout:float=0.01, # Droupout regularization ratio for the encoder
        fc_dropout:float=0.1, # Droupout regularization ratio for the head
        use_bn:bool=True, # indicates if batchnorm will be applied to the model head.
        flatten:bool=True, # this will flatten the output of the encoder before applying the head if True.
        custom_head:Any=None, # custom head that will be applied to the model head (optional).
        ):
        ""

        # Backbone
        backbone = ConvTranBackbone(c_in, seq_len, emb_size=emb_size, num_heads=num_heads, dim_ff=dim_ff, 
                                    abs_pos_encode=abs_pos_encode, rel_pos_encode=rel_pos_encode, dropout=encoder_dropout)

        # Head
        self.head_nf = emb_size
        if custom_head is not None: 
            if isinstance(custom_head, nn.Module): head = custom_head
            else: head = custom_head(self.head_nf, c_out, seq_len)
        elif d is not None:
            head = lin_nd_head(self.head_nf, c_out, seq_len=seq_len, d=d, use_bn=use_bn, fc_dropout=fc_dropout, flatten=flatten)
        else:
            head = nn.Sequential(nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(self.head_nf, c_out))

        layers = OrderedDict([('backbone', backbone), ('head', head)])
        super().__init__(layers) 

ConvTran = ConvTranPlus
