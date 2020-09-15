#
# Import Package


import os
import shutil
import gc
import time
import random as rn
import numpy as np
import pandas as pd
import warnings
import csv

import scipy.io as sio
from scipy import signal
from tqdm import tqdm
import matplotlib.pyplot as plt

from scipy import sparse
from sklearn.metrics import f1_score
from sklearn.model_selection import KFold, StratifiedKFold

# import wfdb
# import wfdb.processing as wp
# from utils import extract_basic_features
# from utils import find_noise_features, extract_basic_features
# from lightgbm import LGBMClassifier
# from xgboost import XGBClassifier

''' '''
# from resnet_ecg.utils import one_hot,get_batches
from resnet_ecg.ecg_preprocess import ecg_preprocessing
from resnet_ecg.densemodel import Net
from keras.utils import to_categorical
from keras.optimizers import SGD, Adam
from keras.callbacks import ModelCheckpoint, LearningRateScheduler, EarlyStopping, ReduceLROnPlateau, TensorBoard
import tensorflow as tf
import keras.backend.tensorflow_backend as KTF
import keras.backend as K
from keras.layers import Input
from keras.models import Model, load_model
import keras
import pywt


warnings.filterwarnings("ignore")

'''
config = tf.ConfigProto(intra_op_parallelism_threads=1, inter_op_parallelism_threads=1)
config.gpu_options.per_process_gpu_memory_fraction = 0.8
session = tf.Session(config=config)
KTF.set_session(session)
'''
os.environ['PYTHONHASHSEED'] = '0'
np.random.seed(42)
rn.seed(12345)
tf.set_random_seed(1234)


# path of training data
path = '/media/jdcloud/'#"/media/uuser/data/finals/"#'/media/jdcloud/'

class Config(object):
    def __init__(self):
        self.conv_subsample_lengths = [1, 2, 1, 2, 1, 2, 1, 2]
        self.conv_filter_length = 32
        self.conv_num_filters_start = 12
        self.conv_init = "he_normal"
        self.conv_activation = "relu"
        self.conv_dropout = 0.5
        self.conv_num_skip = 2
        self.conv_increase_channels_at = 2
        self.batch_size = 32  # 128
        self.input_shape = [2560, 12]  # [1280, 1]
        self.num_categories = 2

    @staticmethod
    def lr_schedule(epoch):
        lr = 0.1
        if epoch >= 10 and epoch < 20:
            lr = 0.01
        if epoch >= 20:
            lr = 0.001
        # print('Learning rate: ', lr)
        return lr

def wavelet(ecg, wavefunc, lv, m, n):  #

    coeff = pywt.wavedec(ecg, wavefunc, mode='sym', level=lv)  #
    # sgn = lambda x: 1 if x > 0 else -1 if x < 0 else 0

    for i in range(m, n + 1):
        cD = coeff[i]
        for j in range(len(cD)):
            Tr = np.sqrt(2 * np.log(len(cD)))
            if cD[j] >= Tr:
                coeff[i][j] = np.sign(cD[j]) - Tr
            else:
                coeff[i][j] = 0

    denoised_ecg = pywt.waverec(coeff, wavefunc)
    return denoised_ecg


def wavelet_db6(sig):
    """
    R J, Acharya U R, Min L C. ECG beat classification using PCA, LDA, ICA and discrete
     wavelet transform[J].Biomedical Signal Processing and Control, 2013, 8(5): 437-448.
    param sig: 1-D numpy Array
    return: 1-D numpy Array
    """
    coeffs = pywt.wavedec(sig, 'db6', level=9)
    coeffs[-1] = np.zeros(len(coeffs[-1]))
    coeffs[-2] = np.zeros(len(coeffs[-2]))
    coeffs[0] = np.zeros(len(coeffs[0]))
    sig_filt = pywt.waverec(coeffs, 'db6')
    return sig_filt


def precision(y_true, y_pred):
    # Calculates the precision
    true_positives = K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))
    predicted_positives = K.sum(K.round(K.clip(y_pred, 0, 1)))
    precision = true_positives / (predicted_positives + K.epsilon())
    return precision


def recall(y_true, y_pred):
    # Calculates the recall
    true_positives = K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))
    possible_positives = K.sum(K.round(K.clip(y_true, 0, 1)))
    recall = true_positives / (possible_positives + K.epsilon())
    return recall


def fbeta_score(y_true, y_pred, beta=1):
    # Calculates the F score, the weighted harmonic mean of precision and recall.
    if beta < 0:
        raise ValueError('The lowest choosable beta is zero (only precision).')

    # If there are no true positives, fix the F score at 0 like sklearn.
    if K.sum(K.round(K.clip(y_true, 0, 1))) == 0:
        return 0

    p = precision(y_true, y_pred)
    r = recall(y_true, y_pred)
    bb = beta ** 2
    fbeta_score = (1 + bb) * (p * r) / (bb * p + r + K.epsilon())
    return fbeta_score


def fmeasure(y_true, y_pred):
    # Calculates the f-measure, the harmonic mean of precision and recall.
    return fbeta_score(y_true, y_pred, beta=1)


def read_data_seg(data_path, split="Train", preprocess=False, fs=500, newFs=256, winSecond=10, winNum=10, n_index=0,pre_type="sym"):
    """ Read data """

    # Fixed params
    # n_index = 0
    n_class = 9
    winSize = winSecond * fs
    new_winSize = winSecond * newFs
    # Paths
    path_signals = os.path.join(data_path, split)

    # Read labels and one-hot encode
    # label_path = os.path.join(data_path, "reference.txt")
    # labels = pd.read_csv(label_path, sep='\t',header = None)
    # labels = pd.read_csv("reference.csv")

    # Read time-series data
    channel_files = os.listdir(path_signals)
    # print(channel_files)
    channel_files.sort()
    n_channels = 12  # len(channel_files)
    # posix = len(split) + 5

    # Initiate array
    list_of_channels = []

    X = np.zeros((len(channel_files), new_winSize, n_channels)).astype('float32')
    i_ch = 0

    channel_name = ['V6', 'aVF', 'I', 'V4', 'V2', 'aVL', 'V1', 'II', 'aVR', 'V3', 'III', 'V5']
    channel_mid_name = ['II', 'aVR', 'V2', 'V5']
    channel_post_name = ['III', 'aVF', 'V3', 'V6']

    for i_ch, fil_ch in enumerate(channel_files[:]):  # tqdm

        if i_ch % 1000 == 0:
            print(i_ch)

        ecg = sio.loadmat(os.path.join(path_signals, fil_ch))
        ecg_length = ecg["I"].shape[1]

        if ecg_length > fs * winNum * winSecond:
            print(" too long !!!", ecg_length)
            ecg_length = fs * winNum * winSecond
        if ecg_length < 4500:
            print(" too short !!!", ecg_length)
            break

        slide_steps = int((ecg_length - winSize) / winSecond)

        if ecg_length <= 4500:
            slide_steps = 0

        ecg_channels = np.zeros((new_winSize, n_channels)).astype('float32')

        for i_n, ch_name in enumerate(channel_name):

            ecg_channels[:, i_n] = signal.resample(ecg[ch_name]
                                                   [:, n_index * slide_steps:n_index * slide_steps + winSize].T
                                                   , new_winSize).T
            if preprocess:
                if pre_type == "sym":
                    ecg_channels[:, i_n] = ecg_preprocessing(ecg_channels[:, i_n].reshape(1, new_winSize), 'sym8', 8, 3,
                                                             newFs, removebaseline=False, normalize=False)[0]
                elif pre_type == "db4":
                    ecg_channels[:, i_n] = wavelet(ecg_channels[:, i_n], 'db4', 4, 2, 4)
                elif pre_type == "db6":
                    ecg_channels[:, i_n] = wavelet_db6(ecg_channels[:, i_n])

                # ecg_channels[:, i_n] = (ecg_channels[:, i_n]-np.mean(ecg_channels[:, i_n]))/np.std(ecg_channels[:, i_n])
            else:
                pass
                print(" no preprocess !!! ")

        X[i_ch, :, :] = ecg_channels

    return X


def preprocess_y(labels, y, num_class=10):
    bin_label = np.zeros((len(y), num_class)).astype('int8')
    for i in range(len(y)):
        label_nona = labels.loc[y[i]].dropna()
        for j in range(1, label_nona.shape[0]):
            bin_label[i, int(label_nona[j])] = 1
    return bin_label


class DataGenerator11(keras.utils.Sequence):
    # ' Generates data for Keras '

    def __init__(self, list_IDs, labels, batch_size=32, dim=(32,32,32), n_channels=1,
                 n_classes=10, shuffle=True):
        # 'Initialization'
        self.dim = dim
        self.batch_size = batch_size
        self.labels = labels
        self.list_IDs = list_IDs
        self.n_channels = n_channels
        self.n_classes = n_classes
        self.shuffle = shuffle
        self.on_epoch_end()

    def __len__(self):
        # 'Denotes the number of batches per epoch'
        return int(np.floor(len(self.list_IDs) / self.batch_size))

    def __getitem__(self, index):
        # 'Generate one batch of data'
        # Generate indexes of the batch
        indexes = self.indexes[index*self.batch_size:(index+1)*self.batch_size]

        # Find list of IDs
        list_IDs_temp = [self.list_IDs[k] for k in indexes]

        # Generate data
        X, y = self.__data_generation(list_IDs_temp)

        return X, y

    def on_epoch_end(self):
        # 'Updates indexes after each epoch'
        self.indexes = np.arange(len(self.list_IDs))
        if self.shuffle == True:
            np.random.shuffle(self.indexes)

    def __data_generation(self, list_IDs_temp):
        # 'Generates data containing batch_size samples' # X : (n_samples, *dim, n_channels)
        # Initialization
        X = np.empty((self.batch_size,  *self.dim, self.n_channels))
        y = np.empty((self.batch_size, self.n_classes), dtype=int)
        #print(len(list_IDs_temp))
        # Generate data
        for i, ID in enumerate(list_IDs_temp):
            # Store sample
            X[i,] = np.load("training_data/" + ID+".npy")
            #ecg = sio.loadmat("preliminary/TRAIN/" + ID)
            #X[i,] = signal.resample(ecg['data'].T,2560)
            #print(ID.strip(".mat"))
            # Store class
            #print(ID.split("_")[0])
            #print(X[i,].shape)
            #print(preprocess_y(self.labels,self.labels[self.labels["File_name"]==ID.split("_")[0]].index))
            y[i,:] = preprocess_y(self.labels,self.labels[self.labels["File_name"] == ID.split("_")[0]].index)
            #print(y[i,:])
        # X_list = [(X[:, i]-np.mean(X[:, i]))/np.std(X[:, i]) for i in range(10)]
        X_list = [X[:, 0], X[:, 1], X[:, 2], X[:, 3], X[:, 4], X[:, 5], X[:, 6], X[:, 7], X[:, 8], X[:, 9]]
        #print(X[:].shape)
        #print(y)
        del X
        #print(y.shape)
        return X_list, y  # keras.utils.to_categorical(y, num_classes=self.n_classes)
class DataGenerator(keras.utils.Sequence):
    # ' Generates data for Keras '

    def __init__(self, list_IDs, labels, quarter_labels,batch_size=32, dim=(32,32,32), n_channels=1,
                 n_classes=10, shuffle=True):
        # 'Initialization'
        self.dim = dim
        self.batch_size = batch_size
        self.labels = labels
        self.quarter_labels = quarter_labels
        self.list_IDs = list_IDs
        self.n_channels = n_channels
        self.n_classes = n_classes
        self.shuffle = shuffle
        self.on_epoch_end()

    def __len__(self):
        # 'Denotes the number of batches per epoch'
        return int(np.floor(len(self.list_IDs) / self.batch_size))

    def __getitem__(self, index):
        # 'Generate one batch of data'
        # Generate indexes of the batch
        indexes = self.indexes[index*self.batch_size:(index+1)*self.batch_size]

        # Find list of IDs
        list_IDs_temp = [self.list_IDs[k] for k in indexes]

        # Generate data
        X, y = self.__data_generation(list_IDs_temp)

        return X, y

    def on_epoch_end(self):
        # 'Updates indexes after each epoch'
        self.indexes = np.arange(len(self.list_IDs))
        if self.shuffle == True:
            np.random.shuffle(self.indexes)

    def __data_generation(self, list_IDs_temp):
        # 'Generates data containing batch_size samples' # X : (n_samples, *dim, n_channels)
        # Initialization
        X = np.empty((self.batch_size,  *self.dim, self.n_channels))
        y = np.empty((self.batch_size, self.n_classes), dtype=int)

        # Generate data
        for i, ID in enumerate(list_IDs_temp):
            IDsplit = ID.split("_")
            if IDsplit[0] == "quarter":
                X[i,] = np.load("/media/uuser/data/final_run/training_data/" + ID.split("_")[1] + ".npy")

                y[i,:] = preprocess_y(self.quarter_labels,self.quarter_labels[self.quarter_labels["File_name"] == IDsplit[0]+"_"+IDsplit[1]].index)

            else:
                # Store sample
                X[i,] = np.load("training_data/" + ID+".npy")
                # Store class
                y[i,:] = preprocess_y(self.labels,self.labels[self.labels["File_name"] == ID.split("_")[0]].index)

        # X_list = [(X[:, i]-np.mean(X[:, i]))/np.std(X[:, i]) for i in range(10)]
        X_list = [X[:, 0], X[:, 1], X[:, 2], X[:, 3], X[:, 4], X[:, 5], X[:, 6], X[:, 7], X[:, 8], X[:, 9]]
        del X

        return X_list, y  # keras.utils.to_categorical(y, num_classes=self.n_classes)


def add_compile(model, config):
    optimizer = SGD(lr=config.lr_schedule(0), momentum=0.9)  # Adam()#
    model.compile(loss='binary_crossentropy',  # weighted_loss,#'binary_crossentropy',
                  optimizer='adam',  # optimizer,#'adam',
                  metrics=['accuracy', fmeasure, precision])  # recall
    # ['accuracy',fbetaMacro,recallMacro,precisionMacro])
    # ['accuracy',fmeasure,recall,precision])



def training_net_one_fold():

    train_dataset_path = path + "/Train/"
    val_dataset_path = path + "/Val/"

    train_files = os.listdir(train_dataset_path)
    train_files.sort()
    val_files = os.listdir(val_dataset_path)
    val_files.sort()

    labels = pd.read_csv(path + "REFERENCE.csv")
    labels_en = pd.read_csv(path + "kfold_labels_en.csv")
    #data_info = pd.read_csv(path + "data_info.csv")

    input_size = (2560, 12)
    net_num = 10
    inputs_list = [Input(shape=input_size) for _ in range(net_num)]
    net = Net()
    outputs = net.nnet(inputs_list, 0.5, num_classes=10)
    model = Model(inputs=inputs_list, outputs=outputs)
    # print(model.summary())

    raw_IDs = labels_en["File_name"].values.tolist()
    extend_db4_IDs = [i + "_db4" for i in raw_IDs]
    extend_db6_IDs = [i + "_db6" for i in raw_IDs]
    all_IDs = raw_IDs + extend_db4_IDs + extend_db6_IDs

    train_labels = labels_en["label1"].values
    all_train_labels = np.hstack((train_labels, train_labels, train_labels))

    # Parameters
    params = {'dim': (10, 2560),
              'batch_size': 64,
              'n_classes': 10,
              'n_channels': 12,
              'shuffle': True}

    en_amount = 1
    model_path = './official_densenet_model/'
    index = np.arange(20067)
    np.random.shuffle(index)

    index_train = index[:14046]
    index_valid = index[14046:]

    tr_IDs = np.array(all_IDs)[index_train]
    val_IDs = np.array(all_IDs)[index_valid]

    print(tr_IDs.shape)
    print(val_IDs.shape)

    # Generators
    training_generator = DataGenerator(tr_IDs, labels, **params)
    validation_generator = DataGenerator(val_IDs, labels, **params)

    checkpointer = ModelCheckpoint(filepath=model_path + 'densenet_extend_weights-best_one_fold_0607.hdf5',
                                   monitor='val_fmeasure', verbose=1, save_best_only=True,
                                   save_weights_only=True,
                                   mode='max')  # val_fmeasure
    reduce = ReduceLROnPlateau(monitor='val_fmeasure', factor=0.5, patience=2, verbose=1, min_delta=1e-4,
                               mode='max')

    earlystop = EarlyStopping(monitor='val_fmeasure', patience=5)

    config = Config()
    add_compile(model, config)

    callback_lists = [checkpointer, reduce]

    history = model.fit_generator(generator=training_generator,
                                  validation_data=validation_generator,
                                  use_multiprocessing=False,
                                  epochs=20,
                                  verbose=1,
                                  callbacks=callback_lists)

def training_net_kfolds():

    train_dataset_path = path + "/Train/"
    val_dataset_path = path + "/Val/"

    train_files = os.listdir(train_dataset_path)
    train_files.sort()
    val_files = os.listdir(val_dataset_path)
    val_files.sort()

    labels = pd.read_csv(path + "REFERENCE.csv")
    labels_en = pd.read_csv(path + "kfold_labels_en.csv")
    #data_info = pd.read_csv(path + "data_info.csv")


    quarter_labels = pd.read_csv("/media/uuser/data/final_run/pro_reference.csv")

    input_size = (2560, 12)
    net_num = 10
    inputs_list = [Input(shape=input_size) for _ in range(net_num)]
    net = Net()
    outputs = net.nnet(inputs_list, 0.5, num_classes=10, attention=False)
    model = Model(inputs=inputs_list, outputs=outputs)
    # print(model.summary())

    raw_IDs = labels_en["File_name"].values.tolist()
    extend_db4_IDs = [i + "_db4" for i in raw_IDs]
    extend_db6_IDs = [i + "_db6" for i in raw_IDs]
    all_IDs = raw_IDs + extend_db4_IDs + extend_db6_IDs

    train_labels = labels_en["label1"].values
    all_train_labels = np.hstack((train_labels, train_labels, train_labels))

    # Parameters
    params = {'dim': (10, 2560),
              'batch_size': 64,
              'n_classes': 10,
              'n_channels': 12,
              'shuffle': True}

    en_amount = 1
    model_path = './official_densenet_model/'

    for seed in range(en_amount):
        print("************************")
        n_fold = 3
        n_classes = 10

        quarter_tr_IDs = []
        quarter_tr_IDs_db4 = []
        quarter_tr_IDs_db6 = []
        
        quarter_val_IDs = []
        quarter_val_IDs_db4 = []
        quarter_val_IDs_db6 = []

        quarter_kfold = StratifiedKFold(n_splits=n_fold, shuffle=True, random_state=1234)
        quarter_kf = quarter_kfold.split(quarter_labels["File_name"].values.tolist(), quarter_labels["label1"].values)
        for quarter_i, (quarter_index_train, quarter_index_valid) in enumerate(quarter_kf):
            print('quarter_fold: ', quarter_i + 1, ' training')

            quarter_tr_IDs.append(quarter_labels["File_name"].values[quarter_index_train].tolist()) 
            quarter_val_IDs.append(quarter_labels["File_name"].values[quarter_index_valid].tolist())

            for j in range(4):
                for ids in quarter_labels[quarter_labels.label1==4]["File_name"]:
                    if ids in quarter_tr_IDs:
                        quarter_tr_IDs.append(ids)
            
            for j in range(2):
                for ids in quarter_labels[quarter_labels.label1==7]["File_name"]:
                    if ids in quarter_tr_IDs:
                        quarter_tr_IDs.append(ids)

            quarter_tr_IDs_db4.append([ids+"_db4" for ids in quarter_tr_IDs[quarter_i]])
            quarter_tr_IDs_db6.append([ids+"_db6" for ids in quarter_tr_IDs[quarter_i]])

            quarter_val_IDs_db4.append([ids+"_db4" for ids in quarter_val_IDs[quarter_i]])
            quarter_val_IDs_db6.append([ids+"_db6" for ids in quarter_val_IDs[quarter_i]])


        kfold = StratifiedKFold(n_splits=n_fold, shuffle=True, random_state=1234)
        #kf = kfold.split(all_IDs, all_train_labels)
        kf = kfold.split(labels["File_name"].values.tolist(), labels["label1"].values)

        for i, (index_train, index_valid) in enumerate(kf):
            print('fold: ', i + 1, ' training')
            t = time.time()
            print(index_train)
            #tr_IDs = np.array(all_IDs)[index_train]
            #val_IDs = np.array(all_IDs)[index_valid]
            #print(tr_IDs.shape)
            ''' '''
            tr_IDs = labels["File_name"].values[index_train].tolist() 
            val_IDs = labels["File_name"].values[index_valid].tolist()

            for j in range(4):
                for ids in labels[labels.label1==4]["File_name"]:
                    if ids in tr_IDs:
                        tr_IDs.append(ids)
            
            for j in range(2):
                for ids in labels[labels.label1==7]["File_name"]:
                    if ids in tr_IDs:
                        tr_IDs.append(ids)
            
            for j in range(1):
                for ids in labels[labels.label1==9]["File_name"]:
                    if ids in tr_IDs:
                        tr_IDs.append(ids)

            tr_IDs_db4 = [ids+"_db4" for ids in tr_IDs]
            tr_IDs_db6 = [ids+"_db6" for ids in tr_IDs]

            val_IDs_db4 = [ids+"_db4" for ids in val_IDs]
            val_IDs_db6 = [ids+"_db6" for ids in val_IDs]            

            tr_IDs = tr_IDs+ tr_IDs_db4 + tr_IDs_db6 + quarter_tr_IDs[i] + quarter_tr_IDs_db4[i] + quarter_tr_IDs_db6[i]
            val_IDs = val_IDs + val_IDs_db4 + val_IDs_db6 + quarter_val_IDs[i] + quarter_val_IDs_db4[i] + quarter_val_IDs_db6[i]
            print("tr_IDs : ",len(tr_IDs))
            print("val_IDs : ",len(val_IDs))
	    
            # Generators
            training_generator = DataGenerator(tr_IDs, labels, quarter_labels, **params)
            validation_generator = DataGenerator(val_IDs, labels, quarter_labels, **params)

            checkpointer = ModelCheckpoint(filepath=model_path + 'densenet_extend_weights-best_k{}_r{}_0807_30_add_quarter.hdf5'.format(seed, i),
                                           monitor='val_fmeasure', verbose=1, save_best_only=True,
                                           save_weights_only=True,
                                           mode='max')  # val_fmeasure
            reduce = ReduceLROnPlateau(monitor='val_fmeasure', factor=0.5, patience=2, verbose=1, min_delta=1e-5,
                                       mode='max')

            earlystop = EarlyStopping(monitor='val_fmeasure',
                                      mode="max",
                                      patience=6,
                                      restore_best_weights=True)

            tensorboard = TensorBoard(log_dir="./logs")

            config = Config()
            add_compile(model, config)

            callback_lists = [checkpointer, reduce, earlystop]

            history = model.fit_generator(generator=training_generator,
                                          validation_data=validation_generator,
                                          use_multiprocessing=False,
                                          epochs=30, # 40  # 20
                                          verbose=1,
                                          callbacks=callback_lists)


def predict_net_one_fold():

    pre_type = "sym"

    #labels = pd.read_csv(path + "REFERENCE.csv")
    labels = pd.read_csv("/media/uuser/data/final_run/reference.csv")
    raw_IDs = labels["File_name"].values.tolist()

    IDs = {}
    IDs["sym"] = raw_IDs
    IDs["db4"] = [i + "_db4" for i in raw_IDs]
    IDs["db6"] = [i + "_db6" for i in raw_IDs]

    X = np.empty((6500, 10, 2560, 12))
    for i, ID in enumerate(IDs[pre_type]):
        X[i,] = np.load("/media/uuser/data/final_run/training_data/" + ID + ".npy")
    #train_x = [(X[:, i]-np.mean(X[:, i]))/np.std(X[:, i]) for i in range(10)]
    train_x = [X[:, 0], X[:, 1], X[:, 2], X[:, 3], X[:, 4], X[:, 5], X[:, 6], X[:, 7], X[:, 8], X[:, 9]]

    index = np.arange(6500)
    y_train = preprocess_y(labels, index)

    input_size = (2560, 12)
    net_num = 10
    inputs_list = [Input(shape=input_size) for _ in range(net_num)]
    net = Net()
    outputs = net.nnet(inputs_list, 0.5, num_classes=10, attention=False)
    model = Model(inputs=inputs_list, outputs=outputs)
    # print(model.summary())

    model_path = './official_densenet_model/'
    model_name = "densenet_extend_weights-best_k0_r0_0730_f0819.hdf5"#'densenet_extend_weights-best_one_fold_0607.hdf5'

    model.load_weights(model_path + model_name)
    blend_train = model.predict(train_x)
    '''
    threshold = np.arange(0.1, 0.9, 0.1)
    acc = []
    accuracies = []
    best_threshold = np.zeros(blend_train.shape[1])

    for i in range(blend_train.shape[1]):
        y_prob = np.array(blend_train[:, i])
        for j in threshold:
            y_pred = [1 if prob >= j else 0 for prob in y_prob]
            acc.append(f1_score(y_train[:, i], y_pred, average='macro'))
        acc = np.array(acc)
        index = np.where(acc == acc.max())
        accuracies.append(acc.max())
        best_threshold[i] = threshold[index[0][0]]
        acc = []

    print("best_threshold :", best_threshold)

    y_pred = np.array([[1 if blend_train[i, j] >= best_threshold[j] else 0 for j in range(blend_train.shape[1])]
              for i in range(len(blend_train))])
    print(" train data f1_score  :", f1_score(y_train, y_pred, average='macro'))

    for i in range(10):
        print("f1 score of ab {} is {}".format(i, f1_score(y_train[:, i], y_pred[:, i], average='macro')))

    net_num = 10
    test_x = [read_data_seg(path, split='Val', preprocess=True, n_index=i, pre_type=pre_type) for i in range(net_num)]

    out = model.predict(test_x)
    y_pred_test = np.array(
        [[1 if out[i, j] >= best_threshold[j] else 0 for j in range(out.shape[1])] for i in range(len(out))])

    classes = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

    test_y = y_pred_test

    y_pred = [[1 if test_y[i, j] >= best_threshold[j] else 0 for j in range(test_y.shape[1])]
              for i in range(len(test_y))]
    pred = []
    for j in range(test_y.shape[0]):
        pred.append([classes[i] for i in range(10) if y_pred[j][i] == 1])

    val_dataset_path = path + "/Val/"
    val_files = os.listdir(val_dataset_path)
    val_files.sort()

    with open('answers_densenet_{}_one_fold_0731.csv'.format(pre_type), 'w') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['File_name', 'label1', 'label2',
                         'label3', 'label4', 'label5', 'label6', 'label7', 'label8', 'label9', 'label10'])
        count = 0
        for file_name in val_files:
            if file_name.endswith('.mat'):

                record_name = file_name.strip('.mat')
                answer = []
                answer.append(record_name)

                result = pred[count]

                answer.extend(result)
                for i in range(10 - len(result)):
                    answer.append('')
                count += 1
                writer.writerow(answer)
        csvfile.close()
    '''
    train_pd0 = pd.DataFrame(blend_train)
    csv_path = "/media/jdcloud/quarter_final/"
    train_pd0.to_csv(csv_path+"densenet_f0819_10net_fold.csv",index=None)

    '''
    test_pd0 = pd.DataFrame(out)
    csv_path = "/media/jdcloud/test_csv/"
    test_pd0.to_csv(csv_path+"densenet_f0819_10net_fold.csv",index=None)
    '''
def predcit_net_kfolds():

    pre_type = "db6"# "sym"

    labels = pd.read_csv(path + "REFERENCE.csv")
    raw_IDs = labels["File_name"].values.tolist()

    IDs = {}
    IDs["sym"] = raw_IDs
    IDs["db4"] = [i + "_db4" for i in raw_IDs]
    IDs["db6"] = [i + "_db6" for i in raw_IDs]

    input_size = (2560, 12)
    net_num = 10
    inputs_list = [Input(shape=input_size) for _ in range(net_num)]
    net = Net()
    outputs = net.nnet(inputs_list, 0.5, num_classes=10 , attention=False)
    model = Model(inputs=inputs_list, outputs=outputs)

    net_num = 10
    test_x = [read_data_seg(path, split='Val', preprocess=True, n_index=i, pre_type=pre_type) for i in range(net_num)]

    model_path = './official_densenet_model/'
    model_name = 'densenet_extend_weights-best_one_fold.hdf5'

    en_amount = 1
    for seed in range(en_amount):
        print("************************")
        n_fold = 3  # 3
        n_classes = 10

        kfold = StratifiedKFold(n_splits=n_fold, shuffle=True, random_state=2019)
        kf = kfold.split(IDs[pre_type], labels['label1'])

        blend_train = np.zeros((6689, n_fold, n_classes)).astype('float32')  # len(train_x)
        blend_test = np.zeros((558, n_fold, n_classes)).astype('float32')  # len(test_x)

        tr_IDs = np.array(IDs[pre_type]) # [index_train]
        # val_IDs = np.array(IDs[pre_type])[index_valid]
        print(tr_IDs.shape)

        X = np.empty((tr_IDs.shape[0], 10, 2560, 12))
        for j, ID in enumerate(tr_IDs):
            X[j, ] = np.load("training_data/" + ID + ".npy")
        # X_tr = [(X[:, i] - np.mean(X[:, i])) / np.std(X[:, i]) for i in range(10)]
        X_tr = [X[:, 0], X[:, 1], X[:, 2], X[:, 3], X[:, 4], X[:, 5], X[:, 6], X[:, 7], X[:, 8], X[:, 9]]
        # print(X.shape)
        del X
        gc.collect()

        count = 0

        for i, (index_train, index_valid) in enumerate(kf):
            print('fold: ', i + 1, ' training')
            t = time.time()

            # Evaluate best trained model
            model.load_weights(model_path + 'densenet_extend_weights-best_k{}_r{}_0806_30.hdf5'.format(seed, i))
            #densenet_extend_weights-best_k{}_r{}_0806_30.hdf5

            blend_train[:, i, :] = model.predict(X_tr)
            blend_test[:, i, :] = model.predict(test_x)

            #del X_tr
            
            #gc.collect()
            count += 1

    index = np.arange(6689)
    y_train = preprocess_y(labels, index)

    train_y = 0.1 * blend_train[:, 0, :] + 0.1 * blend_train[:, 1, :] + 0.8 * blend_train[:, 2, :]   #0.1  0.1  0.8

    threshold = np.arange(0.1, 0.9, 0.1)
    acc = []
    accuracies = []
    best_threshold = np.zeros(train_y.shape[1])

    for i in range(train_y.shape[1]):
        y_prob = np.array(train_y[:, i])
        for j in threshold:
            y_pred = [1 if prob >= j else 0 for prob in y_prob]
            acc.append(f1_score(y_train[:, i], y_pred, average='macro'))
        acc = np.array(acc)
        index = np.where(acc == acc.max())
        accuracies.append(acc.max())
        best_threshold[i] = threshold[index[0][0]]
        acc = []

    print("best_threshold :", best_threshold)

    y_pred = np.array([[1 if train_y[i, j] >= best_threshold[j] else 0 for j in range(train_y.shape[1])]
              for i in range(len(train_y))])
    print(" train data f1_score  :", f1_score(y_train, y_pred, average='macro'))

    for i in range(10):
        print("f1 score of ab {} is {}".format(i, f1_score(y_train[:, i], y_pred[:, i], average='macro')))


    out = 0.1 * blend_test[:, 0, :] + 0.1 * blend_test[:, 1, :] + 0.8 * blend_test[:, 2, :]        #0.1  0.1  0.8

    y_pred_test = np.array(
        [[1 if out[i, j] >= best_threshold[j] else 0 for j in range(out.shape[1])] for i in range(len(out))])

    classes = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

    test_y = y_pred_test

    y_pred = [[1 if test_y[i, j] >= best_threshold[j] else 0 for j in range(test_y.shape[1])]
              for i in range(len(test_y))]
    pred = []
    for j in range(test_y.shape[0]):
        pred.append([classes[i] for i in range(10) if y_pred[j][i] == 1])


    val_dataset_path = path + "/Val/"
    val_files = os.listdir(val_dataset_path)
    val_files.sort()

    with open('answers_densenet_{}_kfold_0807.csv'.format(pre_type), 'w') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['File_name', 'label1', 'label2',
                         'label3', 'label4', 'label5', 'label6', 'label7', 'label8', 'label9', 'label10'])
        count = 0
        for file_name in val_files:
            if file_name.endswith('.mat'):

                record_name = file_name.strip('.mat')
                answer = []
                answer.append(record_name)

                result = pred[count]

                answer.extend(result)
                for i in range(10 - len(result)):
                    answer.append('')
                count += 1
                writer.writerow(answer)
        csvfile.close()

    train_pd0 = pd.DataFrame(blend_train[:,0,:])
    train_pd1 = pd.DataFrame(blend_train[:,1,:])
    train_pd2 = pd.DataFrame(blend_train[:,2,:])
    csv_path = "/media/jdcloud/ensemble_csv/"
    train_pd0.to_csv(csv_path+"densenet_4block_10net_fold0.csv",index=None)
    train_pd1.to_csv(csv_path+"densenet_4block_10net_fold1.csv",index=None)
    train_pd2.to_csv(csv_path+"densenet_4block_10net_fold2.csv",index=None)

    test_pd0 = pd.DataFrame(blend_test[:,0,:])
    test_pd1 = pd.DataFrame(blend_test[:,1,:])
    test_pd2 = pd.DataFrame(blend_test[:,2,:])
    csv_path = "/media/jdcloud/test_csv/"
    test_pd0.to_csv(csv_path+"densenet_4block_10net_fold0.csv",index=None)
    test_pd1.to_csv(csv_path+"densenet_4block_10net_fold1.csv",index=None)
    test_pd2.to_csv(csv_path+"densenet_4block_10net_fold2.csv",index=None)

if __name__ == '__main__':

    # training_net_one_fold()
    # predict_net_one_fold()

    #training_net_kfolds()
    predcit_net_kfolds()

    print("come on ")
