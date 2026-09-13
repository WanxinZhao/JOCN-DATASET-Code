import numpy as np
import pandas as pd 
import matplotlib.pyplot as plt 
from pandas import read_excel, read_csv 
from keras.models import Sequential 
from keras.layers import Dense, Dropout, Activation
from keras.layers import LSTM, GRU, Bidirectional
# import tensorflow as tf
# from tensorflow.keras.models import Sequential
# from tensorflow.keras.layers import Dense, Dropout, Activation, LSTM, Input
from sklearn.preprocessing import MinMaxScaler, StandardScaler  
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
import time
from sklearn.metrics import roc_curve, auc
from sklearn.metrics import roc_auc_score
# import keras_metrics as km
from keras import backend
import random

plt.figure(figsize=(14,7))

def rmse(y_true, y_pred):
	return backend.sqrt(backend.mean(backend.square(y_pred - y_true), axis=-1))


df = read_csv('C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/voyager.csv',usecols = ['Voyager_ch5_BER'],header = 0)
# df = read_excel('C:/Users/po19996/OneDrive - University of Bristol/Desktop/test.xlsx', usecols = [9], sheet_name = 'Sheet2', header = 0)
#df = read_excel('C:/Users/po19996/OneDrive - University of Bristol/Desktop/Journal/test/model/normal/channel_100_LSTM.xlsx',usecols = [1], sheet_name = 'Sheet1', header = 0)

# df = df.iloc[:,5:9]
dataset = df.values
dataset = dataset.astype('float')
#scaler = MinMaxScaler(feature_range = (0,1))
scaler = StandardScaler()
dataset = scaler.fit_transform(dataset)

timestep = 15
max_train_samples = 2000
random_seed = 42
raw_data=dataset

X = []
Y = []

for i in range(len(raw_data)- (timestep)):
    X.append(raw_data[i:i+timestep])
    Y.append(raw_data[i+timestep])


X=np.asanyarray(X)
Y=np.asanyarray(Y)

train_size = min(max_train_samples, len(X))
if train_size == 0:
    raise ValueError('Not enough samples to build a training set.')

rng = np.random.default_rng(random_seed)
all_indices = np.arange(len(X))
train_indices = np.sort(rng.choice(all_indices, size=train_size, replace=False))
test_mask = np.ones(len(X), dtype=bool)
test_mask[train_indices] = False
test_indices = all_indices[test_mask]

Xtrain = X[train_indices,:,:]  
Ytrain = Y[train_indices] 
Xtest = X[test_indices,:,:]  
Ytest= Y[test_indices]  

print("Xtrain shape", Xtrain.shape)

# time_steps = 10
num_features = 1

#create and fit the LSTM network 
model = Sequential()
model.add(LSTM(16, activation='relu', input_shape = (timestep, num_features), return_sequences=True))
#model.add(Dropout(0.2))
model.add(LSTM(16, activation='relu'))
#model.add(Dropout(0.2))
model.add(Dense(1))
model.compile(loss='mse', optimizer= 'adam', metrics = ['mse', 'mae'])
model.summary()
start = time.time()
hist = model.fit(Xtrain,Ytrain, validation_data = (Xtest, Ytest), epochs = 100,batch_size = 20,verbose = 2)
# hist = model.fit(Xtrain,Ytrain, epochs = 40,batch_size = 256,verbose = 2)
end = time.time()
print("Train time: %.4f" % (end-start))


plt.plot(hist.history['mae'])
plt.plot(hist.history['val_mae'])
plt.title('Model Mean Absolute Error')
plt.ylabel('Mean Absolute Error')
plt.xlabel('Epoch')
plt.legend(['Train', 'Val'], loc='upper right')
plt.show()

plt.plot(hist.history['loss'])
plt.plot(hist.history['val_loss'])
plt.title('Model loss')
plt.ylabel('Loss')
plt.xlabel('Epoch')
plt.legend(['Train', 'Val'], loc='upper right')
plt.show()

trainPredict = model.predict(Xtrain)
trainPredict = scaler.inverse_transform(trainPredict)

testPredict = model.predict(Xtest)
testPredict = scaler.inverse_transform(testPredict)


Ytest=np.asanyarray(Ytest)  
Ytest=Ytest.reshape(-1,1) 
Ytest = scaler.inverse_transform(Ytest)


Ytrain=np.asanyarray(Ytrain)  
Ytrain=Ytrain.reshape(-1,1) 
Ytrain = scaler.inverse_transform(Ytrain)


MSE = mean_squared_error(Ytest,testPredict)
print('Test MSE: %.4f MSE' % (MSE))
MAE = mean_absolute_error(Ytest,testPredict)
print('Test MAE: %.4f MAE' % (MAE))
R2 = r2_score(Ytest,testPredict)
print('Test R2: %.4f R2' % (R2))

    
plt.title('LSTM Prediction', fontsize = 20)
plt.plot(Ytest,label='TestingData')
plt.plot(testPredict,label='ForecastingData')
plt.tick_params(labelsize=16) 
# plt.xticks(rotation=30)
plt.legend(fontsize = 16,loc='upper right')
plt.grid(linestyle='-.')
# plt.savefig('C:/Users/po19996/OneDrive - University of Bristol/Desktop/save/LSTM_NDFF_channel_1.png',figsize=(14,7), dpi=1000,format='png', bbox_inches='tight')
plt.show()

Y = Ytest - testPredict
Ytestave = np.sum(Ytest)/len(Ytest)
testPredictave = np.sum(testPredict)/len(testPredict)
Ytestv = Ytest - Ytestave

# plot_results_predict(Ytest,testPredict)

plt.title('The transmission Performance', fontsize = 20)
plt.plot(Y)
plt.plot(Ytestv)
plt.plot(Y,label='Performance degradation')
plt.plot(Ytestv,label='Channel variation')
plt.tick_params(labelsize=16) 
# plt.xticks(rotation=30)
plt.legend(fontsize = 16,loc='upper right')
plt.grid(linestyle='-.')
# plt.savefig('C:/Users/po19996/OneDrive - University of Bristol/Desktop/save/performance_channel_NDFF1.png',figsize=(14,7), dpi=1000,format='png', bbox_inches='tight')
plt.show()

model.save('C:/Users/po19996/Downloads/code/ECOC2026/oopt-gnpy/tools/LSTM.h5')