from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import collections
import datetime
import json
import math
import os
import random
import time

import numpy as np
import tensorflow as tf
from data_loader import Loader
from model import Model
from draw.display_room import draw_np_cad, draw_np_color


parser = argparse.ArgumentParser()
parser.add_argument("--mode", default='train', choices=["train", "test", "export"])
parser.add_argument("--output_dir", default=None, help="where to put output files")
parser.add_argument("--seed", type=int)
parser.add_argument("--checkpoint", default=None, help="directory with checkpoint to resume training from or use for testing")
parser.add_argument("--check_step", default=None, help="check_step")

parser.add_argument("--max_epochs", type=int, help="number of training epochs")
parser.add_argument("--summary_freq", type=int, default=10, help="update summaries every summary_freq epochs")
parser.add_argument("--progress_freq", type=int, default=1, help="display progress every progress_freq epochs")
parser.add_argument("--trace_freq", type=int, default=0, help="trace execution every trace_freq steps")
parser.add_argument("--display_freq", type=int, default=1, help="write current training images every display_freq steps")
parser.add_argument("--save_freq", type=int, default=10, help="save model every save_freq epochs, 0 to disable")
parser.add_argument("--validate_freq", type=int, default=10, help="validate every validate_freq epoch")

parser.add_argument("--batch_size", type=int, default=1, help="number of images in batch")
parser.add_argument("--ngf", type=int, default=64, help="number of generator filters in first conv layer")
parser.add_argument("--ndf", type=int, default=64, help="number of discriminator filters in first conv layer")
parser.add_argument("--discrim_freq", type=int, default=50, help="discrim training loop time")
parser.add_argument("--lr_discriminator", type=float, default=0.0002, help="initial learning rate for discriminator")
parser.add_argument("--lr_generator", type=float, default=0.0002, help="initial learning rate for generator")
parser.add_argument("--beta1", type=float, default=0.5, help="momentum term of adam")
parser.add_argument("--lam", type=float, default=10, help="discriminator gradient penalty weight")
parser.add_argument("--l1_weight", type=float, default=100.0, help="weight on L1 term for generator gradient")
parser.add_argument("--gan_weight", type=float, default=1.0, help="weight on GAN term for generator gradient")

parser.add_argument("--pre_train_G_epoch", type=int, default=50, help="generator pre-training epochs")
parser.add_argument("--pre_train_D_epoch", type=int, default=50, help="discriminator pre-training epochs")


a = parser.parse_args()

if not a.output_dir:
    output_prepath = 'output'
    if not os.path.isdir(output_prepath):
        os.makedirs(output_prepath)
    a.output_dir = os.path.join(output_prepath, datetime.datetime.now().strftime("%I_%M%p_on_%B_%d_%Y"))
    image_path = os.path.join(a.output_dir, 'images')
    if not os.path.isdir(image_path):
        os.makedirs(image_path)

loader = Loader(a.batch_size)


def append_index(filename, info):
    index_path = os.path.join(a.output_dir, filename)
    if os.path.exists(index_path):
        index = open(index_path, "a")
    else:
        index = open(index_path, "w")
        index.write("<html><body><table><tr>")
        index.write("<th>step</th><th>input</th><th>output</th><th>target</th></tr>")

    index.write("<tr>")
    index.write("<td>%d</td>" % info['step'])
    for kind in ["inputs", "outputs", "targets"]:
        index.write("<td><img src='images/%s'></td>" % info[kind])
    index.write("</tr>")
    return index_path


def validate(global_step, model, sess):
    fetches = {
        "inputs": model.inputs,
        "outputs": model.outputs,
        'targets': model.targets
    }
    for step in range(loader.nval):
        rooms, layouts = loader.next_batch(1)
        results = sess.run(fetches, {model.inputs: rooms, model.targets: layouts})
        append('validate.html', results, {
            'outputs': '%010d_%03d_outputs.jpg' % (global_step, step),
            'targets': '%010d_%03d_targets.jpg' % (global_step, step),
            'inputs': '%010d_%03d_inputs.jpg' % (global_step, step),
            'step': global_step
        })


def append(html_file, results, names):
    draw_np_cad(results['outputs'],
                os.path.join(a.output_dir, 'images', names['outputs']))
    draw_np_cad(results['targets'],
                os.path.join(a.output_dir, 'images', names['targets']))
    draw_np_color(results['inputs'],
                  os.path.join(a.output_dir, 'images', names['inputs']))
    append_index(html_file, names)


def run():
    if tf.__version__.split('.')[0] != "1":
        raise Exception("Tensorflow version 1 required")

    if a.seed is None:
        a.seed = random.randint(0, 2**31 - 1)

    tf.set_random_seed(a.seed)
    np.random.seed(a.seed)
    random.seed(a.seed)

    if not os.path.exists(a.output_dir):
        os.makedirs(a.output_dir)

    if a.mode == "test":
        if a.checkpoint is None:
            raise Exception("checkpoint required for test mode")

        # load some options from the checkpoint
        options = {"ngf", "ndf"}
        with open(os.path.join(a.checkpoint, "options.json")) as f:
            for key, val in json.loads(f.read()).items():
                if key in options:
                    print("loaded", key, "=", val)
                    setattr(a, key, val)

    for k, v in a._get_kwargs():
        print(k, "=", v)

    with open(os.path.join(a.output_dir, "options.json"), "w") as f:
        f.write(json.dumps(vars(a), sort_keys=True, indent=4))

    model = Model(a, loader.room_nc, loader.room_width)

    tf.summary.scalar("discriminator_loss", model.discrim_loss)
    tf.summary.scalar('predict_fake', tf.squeeze(model.predict_fake))
    tf.summary.scalar('predict_real', tf.squeeze(model.predict_real))
    # tf.summary.scalar("discriminator_gan_loss", model.discrim_GAN_loss)
    # tf.summary.scalar('discriminator_grad_pen', model.grad_pen)

    tf.summary.scalar("generator_loss_GAN", model.gen_loss_GAN)
    tf.summary.scalar("generator_loss_L1", model.gen_loss_L1)
    tf.summary.scalar('category_loss', model.category_loss)
    tf.summary.scalar('rotation_loss', model.rotation_loss)
    tf.summary.scalar("generator_adversarial_loss", model.gen_loss)
    tf.summary.scalar("generator_supervised_loss", model.gen_supervised_loss)

    for var in tf.trainable_variables():
        tf.summary.histogram(var.op.name + "/values", var)

    for grad, var in model.discrim_grads_and_vars + model.gen_grads_and_vars:
        tf.summary.histogram(var.op.name + "/gradients", grad)

    with tf.name_scope("parameter_count"):
        parameter_count = tf.reduce_sum([tf.reduce_prod(tf.shape(v)) for v in tf.trainable_variables()])

    saver = tf.train.Saver(max_to_keep=100)

    a.summary_freq *= loader.ntrain
    a.progress_freq *= loader.ntrain
    a.save_freq *= loader.ntrain
    a.display_freq *= loader.ntrain
    a.validate_freq *= loader.ntrain

    logdir = a.output_dir if a.summary_freq > 0 else None
    sv = tf.train.Supervisor(logdir=logdir, save_summaries_secs=0, saver=None)
    with sv.managed_session() as sess:
        print("parameter_count =", sess.run(parameter_count))

        if a.checkpoint is not None:
            print("loading model from checkpoint")
            # checkpoint = tf.train.latest_checkpoint(a.checkpoint)
            checkpoint = os.path.join(a.checkpoint, a.check_step)
            saver.restore(sess, checkpoint)

        if a.mode == "test":
            validate(-1, model, sess)
        else:

            max_steps = a.max_epochs * loader.ntrain

            for e in range(a.pre_train_G_epoch):
                if e % 10 == 0 or e == a.pre_train_G_epoch - 1:
                    fetches = {"inputs": model.inputs,
                               'outputs': model.outputs,
                               'targets': model.targets}
                    for step in range(loader.ntrain):
                        results = model.pre_train_G(fetches, sess, loader, 1)
                        prefix = -a.pre_train_G_epoch + e
                        append('train.html', results, {
                            'outputs': '%010d_%03d_outputs.jpg' % (prefix, step),
                            'targets': '%010d_%03d_targets.jpg' % (prefix, step),
                            'inputs': '%010d_%03d_inputs.jpg' % (prefix, step),
                            'step': prefix
                        })
                else:
                    model.pre_train_G({}, sess, loader, loader.ntrain)

            model.pre_train_D({}, sess, loader, a.pre_train_D_epoch * loader.ntrain)

            start = time.time()
            for step in range(max_steps):

                def should(freq):
                    return freq > 0 and ((step + 1) % freq == 0 or step == max_steps - 1)

                fetches = {
                    "train": model.discrim_train,
                }
                for _ in range(a.discrim_freq):
                    rooms, layouts = loader.next_batch(0)
                    sess.run(fetches, {model.inputs: rooms, model.targets: layouts})

                fetches = {
                    "train": model.gen_adversarial_train,
                    "global_step": sv.global_step,
                }

                if should(a.summary_freq):
                    fetches["summary"] = sv.summary_op

                if should(a.display_freq):
                    fetches["inputs"] = model.inputs
                    fetches["outputs"] = model.outputs
                    fetches["targets"] = model.targets

                rooms, layouts = loader.next_batch(0)
                results = sess.run(fetches, {model.inputs: rooms, model.targets: layouts})

                if should(a.validate_freq):
                    validate(step, model, sess)

                if should(a.summary_freq):
                    print("recording summary")
                    sv.summary_writer.add_summary(results["summary"], results["global_step"])

                if should(a.display_freq):
                    append('train.html', results, {
                        'step': step,
                        'outputs': '%010d_outputs.jpg' % step,
                        'targets': '%010d_targets.jpg' % step,
                        'inputs': '%010d_inputs.jpg' % step
                    })

                if should(a.progress_freq):
                    # global_step will have the correct step count if we resume from a checkpoint
                    train_epoch = (step + 1) / loader.ntrain
                    train_step = (step + 1) % loader.ntrain
                    rate = (step + 1) / (time.time() - start)
                    remaining = (max_steps - step - 1) / rate
                    print("progress  epoch %d  remaining %dm" % (train_epoch, remaining / 60))

                if should(a.save_freq):
                    print("saving model")
                    saver.save(sess, os.path.join(a.output_dir, "model"), global_step=sv.global_step)

                if sv.should_stop():
                    break

if __name__ == '__main__':
    run()

































