import timm
import functools
import torch.utils.model_zoo as model_zoo
import torch

from .resnet import resnet_encoders
from .dpn import dpn_encoders
from .sam import sam_vit_encoders, SamVitEncoder
from .vgg import vgg_encoders
from .senet import senet_encoders
from .densenet import densenet_encoders
from .inceptionresnetv2 import inceptionresnetv2_encoders
from .inceptionv4 import inceptionv4_encoders
from .efficientnet import efficient_net_encoders
from .mobilenet import mobilenet_encoders
from .xception import xception_encoders
from .timm_efficientnet import timm_efficientnet_encoders
from .timm_resnest import timm_resnest_encoders
from .timm_res2net import timm_res2net_encoders
from .timm_regnet import timm_regnet_encoders
from .timm_sknet import timm_sknet_encoders
from .timm_mobilenetv3 import timm_mobilenetv3_encoders
from .timm_gernet import timm_gernet_encoders
from .mix_transformer import mix_transformer_encoders
from .mobileone import mobileone_encoders

from .timm_universal import TimmUniversalEncoder

from ._preprocessing import preprocess_input

encoders = {}
encoders.update(resnet_encoders)
encoders.update(dpn_encoders)
encoders.update(vgg_encoders)
encoders.update(senet_encoders)
encoders.update(densenet_encoders)
encoders.update(inceptionresnetv2_encoders)
encoders.update(inceptionv4_encoders)
encoders.update(efficient_net_encoders)
encoders.update(mobilenet_encoders)
encoders.update(xception_encoders)
encoders.update(timm_efficientnet_encoders)
encoders.update(timm_resnest_encoders)
encoders.update(timm_res2net_encoders)
encoders.update(timm_regnet_encoders)
encoders.update(timm_sknet_encoders)
encoders.update(timm_mobilenetv3_encoders)
encoders.update(timm_gernet_encoders)
encoders.update(mix_transformer_encoders)
encoders.update(mobileone_encoders)
encoders.update(sam_vit_encoders)


def get_pretrained_settings(encoders: dict, encoder_name: str, weights: str) -> dict:
    """Get pretrained settings for encoder from encoders collection.

    Args:
        encoders: collection of encoders
        encoder_name: name of encoder in collection
        weights: one of ``None`` (random initialization), ``imagenet`` or other pretrained settings

    Returns:
        pretrained settings for encoder

    Raises:
        KeyError: in case of wrong encoder name or pretrained settings name
    """
    try:
        settings = encoders[encoder_name]["pretrained_settings"][weights]
    except KeyError:
        raise KeyError(
            "Wrong pretrained weights `{}` for encoder `{}`. Available options are: {}".format(
                weights,
                encoder_name,
                list(encoders[encoder_name]["pretrained_settings"].keys()),
            )
        )
    return settings


def get_encoder(name, in_channels=3, depth=5, weights=None, output_stride=32, **kwargs):

    if name.startswith("tu-"):
        name = name[3:]
        encoder = TimmUniversalEncoder(
            name=name,
            in_channels=in_channels,
            depth=depth,
            output_stride=output_stride,
            pretrained=weights is not None,
            **kwargs,
        )
        return encoder

    try:
        Encoder = encoders[name]["encoder"]
    except KeyError:
        raise KeyError("Wrong encoder name `{}`, supported encoders: {}".format(name, list(encoders.keys())))

    params = encoders[name]["params"]
    if name.startswith("sam-"):
        params.update(**kwargs)
        params.update(dict(name=name[4:]))
        if depth is not None:
            params.update(depth=depth)
    else:
        params.update(depth=depth)
    encoder = Encoder(**params)

    if weights is not None:
        settings = get_pretrained_settings(encoders, name, weights)
        encoder.load_state_dict(model_zoo.load_url(settings["url"]))

    encoder.set_in_channels(in_channels, pretrained=weights is not None)
    if output_stride != 32:
        encoder.make_dilated(output_stride)

    return encoder


def get_encoder_names():
    return list(encoders.keys())


def get_preprocessing_params(encoder_name, pretrained="imagenet"):

    if encoder_name.startswith("tu-"):
        encoder_name = encoder_name[3:]
        if not timm.models.is_model_pretrained(encoder_name):
            raise ValueError(f"{encoder_name} does not have pretrained weights and preprocessing parameters")
        settings = timm.models.get_pretrained_cfg(encoder_name)
    else:
        all_settings = encoders[encoder_name]["pretrained_settings"]
        if pretrained not in all_settings.keys():
            raise ValueError("Available pretrained options {}".format(all_settings.keys()))
        settings = all_settings[pretrained]

    formatted_settings = {}
    formatted_settings["input_space"] = settings.get("input_space", "RGB")
    formatted_settings["input_range"] = list(settings.get("input_range", [0, 1]))
    formatted_settings["mean"] = list(settings.get("mean"))
    formatted_settings["std"] = list(settings.get("std"))

    return formatted_settings


def get_preprocessing_fn(encoder_name, pretrained="imagenet"):
    params = get_preprocessing_params(encoder_name, pretrained=pretrained)
    return functools.partial(preprocess_input, **params)
