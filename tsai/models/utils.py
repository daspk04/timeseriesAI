# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/100b_models.utils.ipynb (unless otherwise specified).

__all__ = ['apply_idxs', 'SeqEmbed', 'get_embed_size', 'get_layers', 'is_layer', 'is_linear', 'is_bn', 'is_conv_linear',
           'is_affine_layer', 'is_conv', 'has_bias', 'has_weight', 'has_weight_or_bias', 'check_bias', 'check_weight',
           'get_nf', 'ts_splitter', 'transfer_weights', 'build_ts_model', 'build_model', 'create_model',
           'count_parameters', 'build_tsimage_model', 'build_tabular_model', 'create_tabular_model', 'get_clones',
           'split_model', 'output_size_calculator', 'change_model_head', 'naive_forecaster', 'true_forecaster']

# Cell
from copy import deepcopy
from fastai.layers import flatten_model, params, apply_init
from fastai.learner import Learner
from fastai.data.transforms import get_c
from fastai.tabular.model import *
from fastai.callback.schedule import *
from fastai.vision.models.xresnet import *
from ..imports import *
from .layers import *

# Cell
def apply_idxs(o, idxs):
    "Function to apply indices to zarr, dask and numpy arrays"
    if is_zarr(o): return o.oindex[idxs]
    elif is_dask(o): return o[idxs].compute()
    else: return o[idxs]

# Cell
def SeqEmbed(c_in, embed_dim, seq_embed_size=60, norm=False):
    "Embeds subsequences within a long sequence"
    return ConvBlock(c_in, embed_dim, seq_embed_size, stride=seq_embed_size, padding=0, act=None,
                     norm='Batch' if norm else None, bias=norm is None)

# Cell
def get_embed_size(n_cat, rule='log2'):
    if rule == 'log2':
        return int(np.ceil(np.log2(n_cat)))
    elif rule == 'thumb':
        return min(600, round(1.6 * n_cat**0.56)) # fastai's

# Cell
def get_layers(model, cond=noop, full=True):
    if isinstance(model, Learner): model=model.model
    if full: return [m for m in flatten_model(model) if any([c(m) for c in L(cond)])]
    else: return [m for m in model if any([c(m) for c in L(cond)])]

def is_layer(*args):
    def _is_layer(l, cond=args):
        return isinstance(l, cond)
    return partial(_is_layer, cond=args)

def is_linear(l):
    return isinstance(l, nn.Linear)

def is_bn(l):
    types = (nn.BatchNorm1d, nn.BatchNorm2d, nn.BatchNorm3d)
    return isinstance(l, types)

def is_conv_linear(l):
    types = (nn.Conv1d, nn.Conv2d, nn.Conv3d, nn.Linear)
    return isinstance(l, types)

def is_affine_layer(l):
    return has_bias(l) or has_weight(l)

def is_conv(l):
    types = (nn.Conv1d, nn.Conv2d, nn.Conv3d)
    return isinstance(l, types)

def has_bias(l):
    return (hasattr(l, 'bias') and l.bias is not None)

def has_weight(l):
    return (hasattr(l, 'weight'))

def has_weight_or_bias(l):
    return any((has_weight(l), has_bias(l)))

# Cell
def check_bias(m, cond=noop, verbose=False):
    mean, std = [], []
    for i,l in enumerate(get_layers(m, cond=cond)):
        if hasattr(l, 'bias') and l.bias is not None:
            b = l.bias.data
            mean.append(b.mean())
            std.append(b.std())
            pv(f'{i:3} {l.__class__.__name__:15} shape: {str(list(b.shape)):15}  mean: {b.mean():+6.3f}  std: {b.std():+6.3f}', verbose)
    return np.array(mean), np.array(std)

def check_weight(m, cond=noop, verbose=False):
    mean, std = [], []
    for i,l in enumerate(get_layers(m, cond=cond)):
        if hasattr(l, 'weight') and l.weight is not None:
            w = l.weight.data
            mean.append(w.mean())
            std.append(w.std())
            pv(f'{i:3} {l.__class__.__name__:15} shape: {str(list(w.shape)):15}  mean: {w.mean():+6.3f}  std: {w.std():+6.3f}', verbose)
    return np.array(mean), np.array(std)

# Cell
def get_nf(m):
    "Get nf from model's first linear layer in head"
    return get_layers(m[-1], is_linear)[0].in_features

# Cell
def ts_splitter(m):
    "Split of a model between body and head"
    return L(m.backbone, m.head).map(params)

# Cell
def transfer_weights(model, weights_path:Path, device:torch.device=None, exclude_head:bool=True):
    """Utility function that allows to easily transfer weights between models.
    Taken from the great self-supervised repository created by Kerem Turgutlu.
    https://github.com/KeremTurgutlu/self_supervised/blob/d87ebd9b4961c7da0efd6073c42782bbc61aaa2e/self_supervised/utils.py"""

    device = ifnone(device, default_device())
    state_dict = model.state_dict()
    new_state_dict = torch.load(weights_path, map_location=device)
    matched_layers = 0
    unmatched_layers = []
    for name, param in state_dict.items():
        if exclude_head and 'head' in name: continue
        if name in new_state_dict:
            matched_layers += 1
            input_param = new_state_dict[name]
            if input_param.shape == param.shape: param.copy_(input_param)
            else: unmatched_layers.append(name)
        else:
            unmatched_layers.append(name)
            pass # these are weights that weren't in the original model, such as a new head
    if matched_layers == 0: raise Exception("No shared weight names were found between the models")
    else:
        if len(unmatched_layers) > 0:
            print(f'check unmatched_layers: {unmatched_layers}')
        else:
            print(f"weights from {weights_path} successfully transferred!\n")

# Cell
def build_ts_model(arch, c_in=None, c_out=None, seq_len=None, d=None, dls=None, device=None, verbose=False,
                   pretrained=False, weights_path=None, exclude_head=True, cut=-1, init=None, arch_config={}, **kwargs):

    device = ifnone(device, default_device())
    if dls is not None:
        c_in = ifnone(c_in, dls.vars)
        c_out = ifnone(c_out, dls.c)
        seq_len = ifnone(seq_len, dls.len)
        d = ifnone(d, dls.d)
    if d:
        if 'custom_head' not in kwargs.keys():
            kwargs['custom_head'] = partial(lin_nd_head, d=d)
        elif not isinstance(kwargs['custom_head'], nn.Module):
            kwargs['custom_head'] = partial(kwargs['custom_head'], d=d)
    if sum([1 for v in ['RNN_FCN', 'LSTM_FCN', 'RNNPlus', 'LSTMPlus', 'GRUPlus', 'InceptionTime', 'TSiT',
                        'GRU_FCN', 'OmniScaleCNN', 'mWDN', 'TST', 'XCM', 'MLP', 'MiniRocket', 'InceptionRocket']
            if v in arch.__name__]):
        pv(f'arch: {arch.__name__}(c_in={c_in} c_out={c_out} seq_len={seq_len} device={device}, arch_config={arch_config}, kwargs={kwargs})', verbose)
        model = arch(c_in, c_out, seq_len=seq_len, **arch_config, **kwargs).to(device=device)
    elif 'xresnet' in arch.__name__ and not '1d' in arch.__name__:
        pv(f'arch: {arch.__name__}(c_in={c_in} c_out={c_out} device={device}, arch_config={arch_config}, kwargs={kwargs})', verbose)
        model = (arch(c_in=c_in, n_out=c_out, **arch_config, **kwargs)).to(device=device)
    elif 'minirockethead' in arch.__name__.lower():
        pv(f'arch: {arch.__name__}(c_in={c_in} seq_len={seq_len} device={device}, arch_config={arch_config}, kwargs={kwargs})', verbose)
        model = (arch(c_in, c_out, seq_len=1, **arch_config, **kwargs)).to(device=device)
    elif 'rocket' in arch.__name__.lower():
        pv(f'arch: {arch.__name__}(c_in={c_in} seq_len={seq_len} device={device}, arch_config={arch_config}, kwargs={kwargs})', verbose)
        model = (arch(c_in=c_in, seq_len=seq_len, **arch_config, **kwargs)).to(device=device)
    else:
        pv(f'arch: {arch.__name__}(c_in={c_in} c_out={c_out} device={device}, arch_config={arch_config}, kwargs={kwargs})', verbose)
        model = arch(c_in, c_out, **arch_config, **kwargs).to(device=device)

    try:
        model[0], model[1]
        subscriptable = True
    except:
        subscriptable = False
    if hasattr(model, "head_nf"):  head_nf = model.head_nf
    else:
        try: head_nf = get_nf(model)
        except: head_nf = None

    if not subscriptable and 'Plus' in arch.__name__:
        model = nn.Sequential(*model.children())
        model.backbone = model[:cut]
        model.head = model[cut:]

    if pretrained and not ('xresnet' in arch.__name__ and not '1d' in arch.__name__):
        assert weights_path is not None, "you need to pass a valid weights_path to use a pre-trained model"
        transfer_weights(model, weights_path, exclude_head=exclude_head, device=device)

    if init is not None:
        apply_init(model[1] if pretrained else model, init)

    setattr(model, "head_nf", head_nf)
    setattr(model, "__name__", arch.__name__)

    return model

build_model = build_ts_model
create_model = build_ts_model

# Cell
def count_parameters(model, trainable=True):
    if trainable: return sum(p.numel() for p in model.parameters() if p.requires_grad)
    else: return sum(p.numel() for p in model.parameters())

# Cell
@delegates(XResNet.__init__)
def build_tsimage_model(arch, c_in=None, c_out=None, dls=None, pretrained=False, device=None, verbose=False, init=None, arch_config={}, **kwargs):
    device = ifnone(device, default_device())
    if dls is not None:
        c_in = ifnone(c_in, dls.vars)
        c_out = ifnone(c_out, dls.c)

    model = arch(pretrained=pretrained, c_in=c_in, n_out=c_out, **arch_config, **kwargs).to(device=device)
    setattr(model, "__name__", arch.__name__)
    if init is not None:
        apply_init(model[1] if pretrained else model, init)
    return model

# Cell
@delegates(TabularModel.__init__)
def build_tabular_model(arch, dls, layers=None, emb_szs=None, n_out=None, y_range=None, device=None, arch_config={}, **kwargs):
    if device is None: device = default_device()
    if layers is None: layers = [200,100]
    emb_szs = get_emb_sz(dls.train_ds, {} if emb_szs is None else emb_szs)
    if n_out is None: n_out = get_c(dls)
    assert n_out, "`n_out` is not defined, and could not be inferred from data, set `dls.c` or pass `n_out`"
    if y_range is None and 'y_range' in kwargs: y_range = kwargs.pop('y_range')
    model = arch(emb_szs, len(dls.cont_names), n_out, layers, y_range=y_range, **arch_config, **kwargs).to(device=device)

    if hasattr(model, "head_nf"):  head_nf = model.head_nf
    else: head_nf = get_nf(model)
    setattr(model, "__name__", arch.__name__)
    if head_nf is not None: setattr(model, "head_nf", head_nf)
    return model

create_tabular_model = build_tabular_model

# Cell
def get_clones(module, N):
    return nn.ModuleList([deepcopy(module) for i in range(N)])

# Cell
def split_model(m): return m.backbone, m.head

# Cell
@torch.no_grad()
def output_size_calculator(mod, c_in, seq_len=None):
    assert isinstance(mod, nn.Module)
    return_q_len = True
    if seq_len is None:
        seq_len = 50
        return_q_len = False
    try:
        params_0 = list(mod.parameters())[0]
        xb = torch.rand(1, c_in, seq_len, device=params_0.device, dtype=params_0.dtype)
    except:
        xb = torch.rand(1, c_in, seq_len)
    training = mod.training
    mod.eval()
    c_out, q_len = mod(xb).shape[1:]
    mod.training = training
    if return_q_len:
        return c_out, q_len
    else:
        return c_out, None

# Cell
def change_model_head(model, custom_head, **kwargs):
    r"""Replaces a model's head by a custom head as long as the model has a head, head_nf, c_out and seq_len attributes"""
    model.head = custom_head(model.head_nf, model.c_out, model.seq_len, **kwargs)
    return model

# Cell
def naive_forecaster(o, split, horizon=1):
    if is_listy(horizon):
        _f = []
        for h in horizon:
            _f.append(o[np.asarray(split)-h])
        return np.stack(_f)
    return o[np.asarray(split) - horizon]

def true_forecaster(o, split, horizon=1):
    o_true = o[split]
    if is_listy(horizon):
        o_true = o_true[np.newaxis].repeat(len(horizon), 0)
    return o_true