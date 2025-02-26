
import numpy as np
import torch
from torch.utils.data import DataLoader


import source_segment.segmentation_tools.segmentation_models_pytorch as smp
import source_segment.segmentation_tools.segmentation_models_pytorch.utils as smp_utils
import source_segment.segmentation_tools.segmentation_models_pytorch.decoders.custom.SA_Unet as SA_Unet
import source_segment.segmentation_tools.segmentation_models_pytorch.decoders.custom.iternet_model as iternet_model

import source_segment.config as cf
# import source_segment.segmentation_tools_pytorch.tasm as tasm
import source_segment.segmentation_tools.utils as seg_utils
import source_segment.segmentation_tools.data_handling as data_handling
import source_segment.segmentation_tools.segmentation_config as seg_cf

import source_segment.segmentation_tools.pytorch_utils.training_utils as pt_train
import source_segment.segmentation_tools.pytorch_utils.callbacks as pt_callbacks

# find the device to be used for training from tensorflow and set memory growth to true
has_gpu = torch.cuda.is_available()
device_torch = torch.device("cuda" if has_gpu else "cpu")
# num_cpu = multiprocessing.cpu_count() // 4
num_cpu = 2
# print("Number of CPUs: ", num_cpu)

# define initial variables
MODEL_CLASSES = seg_cf.CHOSEN_MASKS.copy()

N_CLASSES = len(seg_cf.CHOSEN_MASKS)

BATCH_SIZE = seg_cf.BATCH_SIZE
HEIGHT = seg_cf.HEIGHT
WIDTH = seg_cf.WIDTH
BACKBONE_NAME = seg_cf.BACKBONE_NAME
WEIGHTS = seg_cf.WEIGHTS
WWO_AUG = seg_cf.WWO_AUG  # train data with and without augmentation
PROB_APPLY_AUGMENTATION = seg_cf.PROB_APPLY_AUGMENTATION
DEVICE = seg_cf.DEVICE


def get_data_generators(batch_size, height, width, classes=MODEL_CLASSES, train_shuffle=True, val_shuffle=True, seed=None):
    TrainSet = data_handling.DataGenerator(
        'train',
        batch_size,
        height,
        width,
        classes=classes,
        augmentation=data_handling.get_training_augmentation(height=height, width=width),
        prob_apply_aug=PROB_APPLY_AUGMENTATION,
        shuffle=train_shuffle,
        seed=seed,
        verbose=False
    )

    ValidationSet = data_handling.DataGenerator(
        'test',
        batch_size,
        height,
        width,
        classes=classes,
        augmentation=data_handling.get_validation_augmentation(height=height, width=width),
        shuffle=val_shuffle,
        seed=seed,
        verbose=False
    )

    return TrainSet, ValidationSet


def get_callbacks(
        optimiser,
        result,
        model,
        defined_callbacks=None,
        continue_training=False,
        other_stats=None
):

    if defined_callbacks is None:
        defined_callbacks = {
            'val': pt_callbacks.Callbacks(optimizer=optimiser,
                                          model_save_path=cf.MODEL_SAVE_PATH_BEST_VAL_LOSS,
                                          training_stats_path=cf.VAL_CALLBACK_OBJ_PATH,
                                          continue_training=continue_training),
            'train': pt_callbacks.Callbacks(optimizer=optimiser,
                                            model_save_path=cf.MODEL_SAVE_PATH_BEST_TRAIN_LOSS,
                                            training_stats_path=cf.TRAIN_CALLBACK_OBJ_PATH,
                                            continue_training=continue_training)
        }

    defined_callbacks['val'].reduce_lr_on_plateau(
        monitor_value=result["val_acc"],
        mode='max',
        factor=seg_cf.REDUCE_LR_FACTOR_VAL,
        patience=seg_cf.REDUCE_LR_PATIENCE_VAL,
        indicator_text="Val LR scheduler: "
    )
    defined_callbacks['train'].reduce_lr_on_plateau(
        monitor_value=result["train_acc"],
        mode='max',
        factor=seg_cf.REDUCE_LR_FACTOR_TRAIN,
        patience=seg_cf.REDUCE_LR_PATIENCE_TRAIN,
        indicator_text="Train LR scheduler: "
    )
    defined_callbacks['train'].model_checkpoint(
        model=model,
        monitor_value=result["train_acc"],
        mode='max',
        other_stats=other_stats,
        indicator_text="Train checkpoint: "
    )
    defined_callbacks['val'].model_checkpoint(
        model=model,
        monitor_value=result["val_acc"],
        mode='max',
        other_stats=other_stats,
        indicator_text="Val checkpoint: "
    )
    stop_flag = defined_callbacks['val'].early_stopping(
        monitor_value=result[seg_cf.EARLY_STOPPING_MONITOR],
        mode=seg_cf.EARLY_STOPPING_MONITOR_MODE,
        patience=seg_cf.EARLY_STOPPING_PATIENCE,
        indicator_text="Early stopping: "
    )
    defined_callbacks['train'].clear_memory()
    print("_________")

    return defined_callbacks, stop_flag


def predict_image(image_np, model):
    device = next(model.parameters()).device  # Get the device the model is on
    image_tensor = torch.from_numpy(image_np).permute(2, 0, 1).unsqueeze(0).float().to(device)  # Move the input tensor to the same device as the model
    model.eval()
    with torch.no_grad():
        output = model(image_tensor)

    return output.cpu()


def get_model_def():
    model = smp.DeepLabV3Plus(
            encoder_name=seg_cf.BACKBONE_NAME,
            encoder_weights=seg_cf.WEIGHTS,
            classes=len(seg_cf.CHOSEN_MASKS),
            activation=seg_cf.ACTIVATION,
        )

    return model


def _train(
        continue_training=False,
        load_weights_for_fine_tune=False,
        activation=seg_cf.ACTIVATION,
        device=seg_cf.DEVICE,
        INITIAL_LR=seg_cf.INITIAL_LR,
        train_model_save_path=cf.MODEL_SAVE_PATH_BEST_TRAIN_LOSS,
        val_model_save_path=cf.MODEL_SAVE_PATH_BEST_VAL_LOSS,
        input_shape=(3, HEIGHT, WIDTH),
):
    """
    Train the model

    :param continue_training: bool, if True, continue training from the last saved model. All training stats, including checkpoint stats will be loaded
    :param load_weights_for_fine_tune: bool, if True, load weights from the last saved model. All training stats will be reset
    """

    TrainSet, ValidationSet = get_data_generators(BATCH_SIZE, HEIGHT, WIDTH, classes=MODEL_CLASSES, train_shuffle=True, val_shuffle=True, seed=None)

    # choose train set with or without augmentation or one with and one without augmentation
    chosen_train_set = TrainSet

    chosen_train_set = DataLoader(chosen_train_set, batch_size=BATCH_SIZE, shuffle=True, num_workers=num_cpu)
    ValidationSet = DataLoader(ValidationSet, batch_size=BATCH_SIZE, shuffle=False)

    print("TrainSet length: ", len(chosen_train_set))
    print("ValidationSet length: ", len(ValidationSet))

    class_weights = seg_utils.get_balancing_class_weights(seg_cf.CHOSEN_MASKS, data_handling.CLASSES_PIXEL_COUNT_DICT)
    class_weights = class_weights[:N_CLASSES]

    # class_weights = [1, 1]  # Can be disabled by setting all weights to 1

    print("class_weights", class_weights)

    model = get_model_def()
    if continue_training or load_weights_for_fine_tune:
        model.load_state_dict(torch.load(train_model_save_path, map_location=device))
    model.to(device)

    # visualize the model
    model.eval()
    visualise_generator(
        data_loader=chosen_train_set,
        model=model,
        device=device,
        num_images=3,
    )

    model.train()
    loss = smp.utils.losses.DiceLoss()

    metrics = [
        smp.utils.metrics.IoU(threshold=0.5, ignore_channels=[0]),
        # smp.utils.metrics.Fscore(threshold=0.5, ignore_channels=[0]),
        smp.utils.metrics.Accuracy(threshold=0.5, ignore_channels=[0]),
        smp.utils.metrics.DiceScore(threshold=0.5, ignore_channels=[0]),
    ]

    if continue_training:
        objects = pt_callbacks.load_saved_objects(cf.VAL_CALLBACK_OBJ_PATH)
        optimizer = objects.optimizer
        other_stats = objects.other_stats
        initial_epoch = other_stats['epochs'] + 1  # +1 because the epoch is incremented at the end of the loop
    else:
        optimizer = torch.optim.Adam([
            dict(params=model.parameters(), lr=seg_cf.INITIAL_LR),
        ])
        initial_epoch = 0

    train_epoch = smp.utils.train.TrainEpoch(
        model,
        loss=loss,
        metrics=metrics,
        optimizer=optimizer,
        device=device,
        verbose=True,
    )

    valid_epoch = smp.utils.train.ValidEpoch(
        model,
        loss=loss,
        metrics=metrics,
        device=device,
        verbose=True,
    )

    defined_callbacks = None
    for i in range(initial_epoch, 99999999):
        print(f'\nEpoch: {i + 1} LR: {optimizer.param_groups[0]["lr"]}\n')
        train_logs = train_epoch.run(chosen_train_set)
        valid_logs = valid_epoch.run(ValidationSet)

        results = {
            'train_acc': train_logs['iou_score'],
            'val_acc': valid_logs['iou_score'],
            'train_loss': train_logs['dice_loss'],
            'val_loss': valid_logs['dice_loss'],
        }

        other_stats = {"epochs": i}

        defined_callbacks, stop_flag = get_callbacks(
            optimiser=optimizer,
            result=results,
            model=model,
            defined_callbacks=defined_callbacks,
            continue_training=continue_training,
            other_stats=other_stats,
        )
        if stop_flag:
            print("Early stopping triggered")
            break


def visualise_generator(
        data_loader,
        model_save_path=cf.MODEL_SAVE_PATH_BEST_TRAIN_LOSS,
        model=None,
        num_images=None,
        run_evaluation=False,
        val_batch_size=1,
        num_workers=0,
        device=DEVICE,
):
    if type(data_loader) == str:
        if data_loader == 'train':
            data_generator = get_data_generators(BATCH_SIZE, HEIGHT, WIDTH, classes=MODEL_CLASSES, train_shuffle=True, val_shuffle=True, seed=None)[0]
        elif data_loader == 'val':
            data_generator = get_data_generators(BATCH_SIZE, HEIGHT, WIDTH, classes=MODEL_CLASSES, train_shuffle=True, val_shuffle=True, seed=None)[1]
    elif type(data_loader) != torch.utils.data.DataLoader:
        raise TypeError("data_loader must be of type str or torch.utils.data.DataLoader, or \"train\" or \"val\"")

    if type(data_loader) != torch.utils.data.DataLoader:
        data_loader = torch.utils.data.DataLoader(data_generator, batch_size=val_batch_size, shuffle=False, num_workers=num_workers)

    if model is None:
        model = get_model_def()
        model.load_state_dict(torch.load(model_save_path, map_location=device))

    model.eval()
    model.to(device)

    # evaluate model on data_loader
    if run_evaluation:
        print("\nEvaluating model on data_loader: ")
        results = pt_train._evaluate(model, data_loader)
        print("Results: ", results)

    cnt = 0
    for batch in data_loader:
        for (image, label) in zip(batch[0], batch[1]):

            print("\nImage: ", image.shape)
            print("Label: ", label.shape)

            # get prediction
            image = image.unsqueeze(0).to(device)
            pred_mask = model(image)

            pred_mask = pred_mask.cpu().detach().numpy()
            pred_mask = np.squeeze(pred_mask, axis=0)
            pred_mask = np.transpose(pred_mask, (1, 2, 0))
            pred_mask = np.argmax(pred_mask, axis=-1)

            label_mask = label.cpu().detach().numpy()
            label_mask = np.transpose(label_mask, (1, 2, 0))
            label_mask = np.argmax(label_mask, axis=-1)

            img_np = image.cpu().numpy()
            img_np = np.squeeze(img_np, axis=0)
            img_np = np.transpose(img_np, (1, 2, 0))
            img_np = img_np * 255.0
            img_np = img_np.astype(np.uint8)

            seg_utils.display([img_np, label_mask, pred_mask],
                              ["Image", "Label", "Prediction"],)

            cnt += 1
            if num_images and cnt >= num_images:
                return


# ReduceLROnPlateau saves the last used lr and model uses it by default. This lets us start from the initial lr
def fix_scheduler_initial_lr(epoch, lr):
    if epoch == 0 and lr != seg_cf.INITIAL_LR:
        return seg_cf.INITIAL_LR
    else:
        return lr


def train(
        continue_training,
        load_weights_for_fine_tune,
):
    data_handling.init()
    _train(continue_training=continue_training, load_weights_for_fine_tune=load_weights_for_fine_tune)


if __name__ == "__main__":
    if has_gpu:
        print("\n======> GPU is available and in use <========\n")
    else:
        print("\n======> GPU is not available, program will still run on the CPU <=====\n")
    print("Num CPUs Available: ", num_cpu)

    data_handling.init()
    # train(continue_training=True, load_weights_for_fine_tune=False)

    visualise_generator(
        data_loader='val',
        model_save_path=cf.MODEL_SAVE_PATH_BEST_VAL_LOSS,
        model=None,
        device="cpu"
    )
