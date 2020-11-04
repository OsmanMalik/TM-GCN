# This version of the amlsim experiment imports data preprocessed in Matlab, and uses the GCN baseline

# Imports and aliases
import pickle
import torch as t
import torch.nn as nn
import torch.nn.functional as F
import torchvision
import torchvision.datasets as datasets
import numpy as np
import matplotlib.pyplot as plt
import cProfile
import pandas as pd
import datetime
from scipy.sparse import csr_matrix
import os.path
import embedding_help_functions as ehf
import scipy.io as sio
unsq = t.unsqueeze
sq = t.squeeze

# Settings
alpha_vec = [.75, .76, .77, .78, .79, .80, .81, .82, .83, .84, .85, .86, .87, .88, .89, .90, .91, .92, .93, .94, .95]
no_layers = 1
no_epochs = 10000
dataset = "amlsim"
mat_f_name = "saved_content_amlsim.mat"
no_trials = 1

data_loc = "data/amlsim/1Kvertices-100Kedges/"
S_train, S_val, S_test = 150, 25, 25
lr = 0.01
momentum = 0.9

# Load stuff from mat file
saved_content = sio.loadmat(data_loc + mat_f_name)
T = np.max(saved_content["A_labels_subs"][:,0])
N1 = np.max(saved_content["A_labels_subs"][:,1])
N2 = np.max(saved_content["A_labels_subs"][:,2])
N = max(N1, N2)
A_sz = t.Size([T, N, N])
C_sz = t.Size([S_train, N, N])
A_labels = t.sparse.FloatTensor(t.tensor(np.array(saved_content["A_labels_subs"].transpose(1,0), dtype=int) - 1, dtype=t.long), sq(t.tensor(saved_content["A_labels_vals"])), A_sz).coalesce()
C = t.sparse.FloatTensor(t.tensor(np.array(saved_content["C_subs"].transpose(1,0), dtype=int), dtype=t.long) - 1, sq(t.tensor(saved_content["C_vals"])), t.Size([T,N,N])).coalesce()

A = t.sparse.FloatTensor(A_labels._indices(), t.ones(A_labels._values().shape), A_sz).coalesce()

# Turn C_train, C_val and C_test into lists of sparse matrices so that we can use them in matrix multiplication...
C_train = []
for j in range(S_train):
	idx = C._indices()[0] == j
	C_train.append(t.sparse.FloatTensor(C._indices()[1:3,idx], C._values()[idx]))	
C_val = []
for j in range(S_train, S_train+S_val):
	idx = C._indices()[0] == j
	C_val.append(t.sparse.FloatTensor(C._indices()[1:3,idx], C._values()[idx]))
C_test = []
for j in range(S_train+S_val, S_train+S_val+S_test):
	idx = C._indices()[0] == j
	C_test.append(t.sparse.FloatTensor(C._indices()[1:3,idx], C._values()[idx]))

# Create features for the nodes
X = t.zeros(A.shape[0], A.shape[1], 2)
X[:, :, 0] = t.sparse.sum(A, 1).to_dense()
X[:, :, 1] = t.sparse.sum(A, 2).to_dense()
X_train = X[0:S_train].double()
X_val = X[S_train:S_train+S_val].double()
X_test = X[S_train+S_val:].double()

# Divide adjacency matrices and labels into training, validation and testing sets
# 	Training
subs_train = A_labels._indices()[0]<S_train
edges_train = A_labels._indices()[:, subs_train]
labels_train = t.sign(A_labels._values()[subs_train])
target_train = (labels_train!=-1).long() # element = 0 if class is -1; and 1 if class is 0 or +1

#	Validation
subs_val = (A_labels._indices()[0]>=S_train) & (A_labels._indices()[0]<S_train+S_val)
edges_val = A_labels._indices()[:, subs_val]
edges_val[0] -= S_train
labels_val = t.sign(A_labels._values()[subs_val])
target_val = (labels_val!=-1).long()

#	Testing
subs_test = (A_labels._indices()[0]>=S_train+S_val) 
edges_test = A_labels._indices()[:, subs_test]
edges_test[0] -= (S_train+S_val)
labels_test = t.sign(A_labels._values()[subs_test])
target_test = (labels_test!=-1).long()

if no_trials > 1:
	ep_acc_loss_vec = []

for tr in range(no_trials):
	for alpha in alpha_vec:
		class_weights = t.tensor([alpha, 1.0-alpha])
		save_res_fname = "results_BASELINE_layers" + str(no_layers) + "_w" + str(round(float(class_weights[0])*100)) + "_" + dataset

		# Create gcn for training
		if no_layers == 2:
			gcn = ehf.EmbeddingKWGCN(C_train, X_train, edges_train, [6,6,2], nonlin2="selu")
		elif no_layers == 1:
			gcn = ehf.EmbeddingKWGCN(C_train, X_train, edges_train, [6,2])

		# Train
		optimizer = t.optim.SGD(gcn.parameters(), lr=lr, momentum=momentum)
		criterion = nn.CrossEntropyLoss(weight=class_weights) # Takes arguments (output, target)
		ep_acc_loss = np.zeros((no_epochs,12)) # (precision_train, recall_train, f1_train, loss_train, precision_val, recall_val, f1_val, loss_val, precision_test, recall_test, f1_test, loss_test)
		for ep in range(no_epochs):
			# Compute loss and take step
			optimizer.zero_grad()
			output_train = gcn()
			loss_train = criterion(output_train, target_train)
			loss_train.backward()
			optimizer.step()

			# Things that don't require gradient
			with t.no_grad():
				guess_train = t.argmax(output_train, dim=1)
				tp = t.sum((guess_train==0)&(target_train==0), dtype=t.float64) # true positive
				fp = t.sum((guess_train==0)&(target_train!=0), dtype=t.float64) # false positive
				fn = t.sum((guess_train!=0)&(target_train==0), dtype=t.float64) # false negative
				precision_train = tp/(tp+fp)
				recall_train = tp/(tp+fn)
				f1_train = 2*(precision_train*recall_train)/(precision_train + recall_train)
				if ep % 100 == 0:
					# Compute stats for validation data
					output_val = gcn(C_val, X_val, edges_val)
					guess_val = t.argmax(output_val, dim=1)
					tp = t.sum((guess_val==0)&(target_val==0), dtype=t.float64) # true positive
					fp = t.sum((guess_val==0)&(target_val!=0), dtype=t.float64) # false positive
					fn = t.sum((guess_val!=0)&(target_val==0), dtype=t.float64) # false negative
					precision_val = tp/(tp+fp)
					recall_val = tp/(tp+fn)
					f1_val = 2*(precision_val*recall_val)/(precision_val + recall_val)
					loss_val = criterion(output_val, target_val)
					
					# Compute stats for test data
					output_test = gcn(C_test, X_test, edges_test)
					guess_test = t.argmax(output_test, dim=1)
					tp = t.sum((guess_test==0)&(target_test==0), dtype=t.float64) # true positive
					fp = t.sum((guess_test==0)&(target_test!=0), dtype=t.float64) # false positive
					fn = t.sum((guess_test!=0)&(target_test==0), dtype=t.float64) # false negative
					precision_test = tp/(tp+fp)
					recall_test = tp/(tp+fn)
					f1_test = 2*(precision_test*recall_test)/(precision_test + recall_test)
					loss_test = criterion(output_test, target_test)

					# Print
					print("Tr/Ep %d/%d. Train precision/recall/f1 %.16f/%.16f/%.16f. Train loss %.16f." % (tr, ep, precision_train, recall_train, f1_train, loss_train))
					print("Tr/Ep %d/%d. Val precision/recall/f1 %.16f/%.16f/%.16f. Val loss %.16f." % (tr, ep, precision_val, recall_val, f1_val, loss_val))
					print("Tr/Ep %d/%d. Test precision/recall/f1 %.16f/%.16f/%.16f. Test loss %.16f.\n" % (tr, ep, precision_test, recall_test, f1_test, loss_test))
				ep_acc_loss[ep] = [precision_train, recall_train, f1_train, loss_train, precision_val, recall_val, f1_val, loss_val, precision_test, recall_test, f1_test, loss_test]

		print("FINAL: Train precision/recall/f1 %.16f/%.16f/%.16f. Train loss %.16f." % (precision_train, recall_train, f1_train, loss_train))
		print("FINAL: Val precision/recall/f1 %.16f/%.16f/%.16f. Val loss %.16f." % (precision_val, recall_val, f1_val, loss_val))
		print("FINAL: Test precision/recall/f1 %.16f/%.16f/%.16f. Test loss %.16f.\n" % (precision_test, recall_test, f1_test, loss_test))

		if no_trials == 1:
			pickle.dump(ep_acc_loss, open(save_res_fname, "wb"))
			print("Results saved for single trial")
		else:
			ep_acc_loss_vec.append(ep_acc_loss)

if no_trials > 1:
	pickle.dump(ep_acc_loss_vec, open(save_res_fname + "_no_trials" + str(no_trials), "wb"))
	print("Results saved for all trials")