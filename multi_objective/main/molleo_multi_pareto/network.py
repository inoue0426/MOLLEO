#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Jul 31 19:25:08 2021

@author: akshat
"""
import copy
import os

import numpy as np
import rdkit
import torch
import torch.nn as nn
import torch.nn.functional as F
from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors
from torch.utils.data import DataLoader, TensorDataset

RDLogger.DisableLog("rdApp.*")

import inspect
import multiprocessing
from collections import OrderedDict
from importlib import reload
from typing import List

# from .features import get_mol_info
from features import get_mol_info
from sklearn.metrics import average_precision_score, roc_auc_score
from transformers import AutoModel, AutoTokenizer

# import wandb
# wandb.init(project='language_guided_genetic_algorithms')

GLOBAL_EPOCH = -1


def get_mol_feature(smi: str) -> np.array:
    """
    Given a SMILES string (smi), a user needs to provide code for creating descriptors
    for the molecules. This will be used as features for the (1) classifier NN and (2)
    the discriminator (if active).

    This function will be parallelised by processes.

    Parameters
    ----------
    smi : str
        Valid SMILE string generated by JANUS.

    Returns
    -------
    descriptor : array
        Property value of SMILES string 'smi'.

    """
    # mol = Chem.MolFromSmiles(smi)
    # descriptor = AllChem.GetMorganFingerprintAsBitVect(mol, 3, 1024)
    descriptor = get_mol_info(smi)

    return np.array(descriptor)


def obtain_features(smi_list: List[str], num_workers: int = 1):
    assert num_workers <= multiprocessing.cpu_count(), "num_workers exceed cpu count"
    with multiprocessing.Pool(num_workers) as pool:
        dataset_x = pool.map(get_mol_feature, smi_list)

    return np.array(dataset_x)


class MLP(nn.Module):
    def __init__(self, h_sizes: List[int], n_input: int, n_output: int):
        super(MLP, self).__init__()
        # Layers
        self.hidden = nn.ModuleList([nn.Linear(n_input, h_sizes[0])])
        for k in range(len(h_sizes) - 1):
            self.hidden.append(nn.Linear(h_sizes[k], h_sizes[k + 1]))
        self.predict = nn.Linear(h_sizes[-1], n_output)

    def forward(self, x):
        for layer in self.hidden:
            x = torch.sigmoid(layer(x))
        output = torch.sigmoid(self.predict(x))
        return output


class EarlyStopping:
    """Class that checks criteria for early stopping. Saves the best model weights."""

    def __init__(self, patience, min_delta, mode="minimize"):
        self.patience = patience
        self.best_weights = None
        self.checkpoint = 0
        self.best_epoch = 0
        self.best_auprc = -1
        self.best_auroc = -1
        if mode == "maximize":
            self.monitor_fn = lambda a, b: np.greater(a - min_delta, b)
            self.best_val = -np.inf
        elif mode == "minimize":
            self.monitor_fn = lambda a, b: np.less(a + min_delta, b)
            self.best_val = np.inf
        else:
            raise ValueError(f"Mode should be either minimize or maximize.")

    def check_criteria(self, net, epoch, new_val, aupr, auroc):
        """Compare with value in memory. If there is an improvement, reset the checkpoint and
        save the model weights.
        Return True if stopping criteria is met (checkpoint is exceeds patience), otherwise, return False.
        """
        if self.monitor_fn(new_val, self.best_val):
            self.best_val = new_val
            self.checkpoint = 0
            self.best_weights = copy.deepcopy(net.state_dict())
            self.best_epoch = epoch
            self.best_auprc = aupr
            self.best_auroc = auroc
        else:
            self.checkpoint += 1

        return self.checkpoint > self.patience

    def restore_best(self, net, verbose=True):
        if verbose:
            print(
                f"        Early stopping at epoch: {self.best_epoch}       loss: {self.best_val}"
            )
        net.load_state_dict(self.best_weights)
        return net


def get_device(use_gpu: bool):
    if use_gpu:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            print("No GPU available, defaulting to CPU.")
    else:
        device = "cpu"

    return device


############################################################################################
# class MolCLIP(nn.Module):
#    def __init__(self, prompt):
#        super(MolCLIP, self).__init__()
#
#
#        input_model_dir="/scratch/ssd004/scratch/mskrt/huggingface_models/MoleculeSTM/demo/demo_checkpoints_SMILES"
#        dataspace_path="/scratch/ssd004/scratch/mskrt/datasets/"
#        vocab_path="/h/mskrt/language_guided_GA/MoleculeSTM/bart_vocab.txt"
#        SSL_emb_dim=256
#        device='cuda'
#
#        # load scibert
#        pretrained_SciBERT_folder = os.path.join(dataspace_path, 'pretrained_SciBERT')
#        text_tokenizer = AutoTokenizer.from_pretrained('allenai/scibert_scivocab_uncased', cache_dir=pretrained_SciBERT_folder)
#        text_model = AutoModel.from_pretrained('allenai/scibert_scivocab_uncased', cache_dir=pretrained_SciBERT_folder).to(device)
#        text_dim = 768
#
#        input_model_path = os.path.join(input_model_dir, "text_model.pth")
#        print("Loading from {}".format(input_model_path))
#        state_dict = torch.load(input_model_path, map_location='cpu')
#        text_model.load_state_dict(state_dict)
#
#        input_model_path = os.path.join(input_model_dir, "molecule_model.pth")
#        print("Loading from {}...".format(input_model_path))
#
#
#        #MegaMolBART_wrapper = MegaMolBART(vocab_path=vocab_path, input_dir=None, output_dir=None)
#        #molecule_model = MegaMolBART_wrapper.model
#        global molecule_model
#        state_dict = torch.load(input_model_path, map_location='cpu')
#        molecule_model.load_state_dict(state_dict)
#        molecule_dim = 256
#
#        text2latent = nn.Linear(text_dim, SSL_emb_dim)
#        input_model_path = os.path.join(input_model_dir, "text2latent_model.pth")
#        print("Loading from {}...".format(input_model_path))
#        state_dict = torch.load(input_model_path, map_location='cpu')
#        text2latent.load_state_dict(state_dict)
#
#        mol2latent = nn.Linear(molecule_dim, SSL_emb_dim)
#        input_model_path = os.path.join(input_model_dir, "mol2latent_model.pth")
#        print("Loading from {}...".format(input_model_path))
#        state_dict = torch.load(input_model_path, map_location='cpu')
#        mol2latent.load_state_dict(state_dict)
#
#        with torch.no_grad():
#            self.text_model = text_model.to(device)
#            self.molecule_model = molecule_model.to(device)
#
#        self.text2latent = text2latent.to(device)
#        self.mol2latent = mol2latent.to(device)
#        self.MegaMolBART_wrapper=MegaMolBART_wrapper
#        self.text_tokenizer=text_tokenizer
#        self.text_prompt=prompt
#        self.device=device
#
#        self.temperature = nn.parameter.Parameter(torch.tensor(0.0), requires_grad=True)
#
#    def forward_batch(self, molecule_data):
#        molecule_data = list(molecule_data) # for SMILES_list
#        molecule_repr = get_molecule_repr_MoleculeSTM(
#                molecule_data, mol2latent=self.mol2latent,
#                molecule_type="SMILES", MegaMolBART_wrapper=self.MegaMolBART_wrapper)
#
#        text_repr = self.get_text_repr([self.text_prompt])
#        output = self.do_CL_eval(text_repr, molecule_repr)
#        output = torch.sigmoid(output)
#        return output
#
#    def get_text_repr(self, text):
#        text_tokens_ids, text_masks = prepare_text_tokens(
#            device=self.device, description=text, tokenizer=self.text_tokenizer, max_seq_len=512)
#        text_output = self.text_model(input_ids=text_tokens_ids, attention_mask=text_masks)
#        text_repr = text_output["pooler_output"]
#        text_repr = self.text2latent(text_repr)
#        return text_repr
#
#    def do_CL_eval(self, X, Y):
#        """
#        X is shape 1 x d
#        Y is shape B x d
#        want output B x 1 --> sum(X * Y)
#        """
#        X = X / X.norm(dim=1,keepdim=True)
#
#        Y = Y / Y.norm(dim=1,keepdim=True)
#
#        logits = torch.exp(self.temperature) * Y @ X.t()
#
#        return logits


def create_network(n_hidden: List[int], n_input: int, n_output: int, device: str):
    """Obtain network

    Parameters:
    n_hidden             (list) : Intermediate discrm layers (e.g. [100, 10])
    device               (str)  : Device discrm. will be initialized

    Returns:
    net : torch model
    optimizer   : Loss function optimized (Adam)
    loss_func   : Loss (Cross-Entropy )
    """
    net = MLP(n_hidden, n_input, n_output).to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=0.001, weight_decay=1e-4)
    loss_func = nn.BCELoss()

    return net, optimizer, loss_func


def train_valid_split(
    data_x: np.array, data_y: np.array, train_ratio: float = 0.8, seed: int = 30624700
):
    """Return a random split of training and validation data.
    Ratio determines the size of training set. Avoids use of sklearn.
    """
    # n = data_x.shape[0]
    n = len(data_x)
    train_n = np.floor(n * train_ratio).astype(int)
    indices = np.random.RandomState(seed=seed).permutation(n)

    train_x = [data_x[i] for i in indices[:train_n]]
    train_y = [data_y[i] for i in indices[:train_n]]

    valid_x = [data_x[i] for i in indices[train_n:]]
    valid_y = [data_y[i] for i in indices[train_n:]]

    # train_x, train_y = data_x[indices[:train_n]], data_y[indices[:train_n]]
    # valid_x, valid_y = data_x[indices[train_n:]], data_y[indices[train_n:]]

    return train_x, train_y, valid_x, valid_y


def do_x_training_steps(
    data_x: np.array,
    data_y: np.array,
    net: nn.Module,
    optimizer: torch.optim.Optimizer,
    loss_func: nn.Module,
    steps: int,
    batch_size: int,
    device: str,
    use_lm: bool,
):
    """Do steps for training. Set batch_size to -1 for full batch training,
    and 1 for SGD.
    """
    global GLOBAL_EPOCH
    train_x, train_y, valid_x, valid_y = train_valid_split(
        data_x, data_y, train_ratio=0.8
    )
    train_y = torch.tensor(train_y, device=device, dtype=torch.float)
    valid_y = torch.tensor(valid_y, device=device, dtype=torch.float)
    if use_lm:
        train_loader = DataLoader(
            list(zip(train_x, train_y)), batch_size=batch_size, shuffle=True
        )
        valid_loader = DataLoader(list(zip(valid_x, valid_y)), batch_size=batch_size)
    else:
        train_x = torch.tensor(train_x, device=device, dtype=torch.float)
        valid_x = torch.tensor(valid_x, device=device, dtype=torch.float)

        train_loader = DataLoader(
            TensorDataset(train_x, train_y), batch_size=batch_size, shuffle=True
        )
        valid_loader = DataLoader(
            TensorDataset(valid_x, valid_y), batch_size=batch_size
        )

    if batch_size == -1:
        batch_size = train_x.shape[0]

    early_stop = EarlyStopping(patience=500, min_delta=1e-7, mode="minimize")
    net.train()
    GLOBAL_EPOCH += 1
    for t in range(steps):
        # training steps
        # print("batch t", t)
        train_loss = 0
        train_samples = 0
        for x, y in train_loader:
            batch_size = len(x)

            # if use_lm:
            #    pred_y = net.forward_batch(x)#.unsqueeze(1)
            # else:
            pred_y = net(x)

            loss = loss_func(pred_y, y)
            train_loss += loss.item() * batch_size
            train_samples += batch_size

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        # wandb.log({'generation': GLOBAL_EPOCH, 'train_loss': train_loss})

        # validation steps
        # val_loss = 0
        all_y = []
        all_preds = []
        net.eval()
        val_samples = 0
        val_loss = 0
        with torch.no_grad():
            for x, y in valid_loader:
                batch_size = len(x)
                # if use_lm:
                #    pred_y = net.forward_batch(x)#.unsqueeze(1)
                # else:
                pred_y = net(x)

                loss = loss_func(pred_y, y)

                all_y.extend(y.squeeze())
                all_preds.extend(pred_y.squeeze())
                val_loss += loss.item() * batch_size
                val_samples += batch_size

        all_y = torch.stack(all_y).detach().cpu().numpy().astype(int)
        all_preds = torch.stack(all_preds).detach().cpu().numpy()

        aupr = average_precision_score(all_y, all_preds)
        auroc = roc_auc_score(all_y, all_preds)
        val_loss /= val_samples

        # wandb.log({'generation': GLOBAL_EPOCH, 'val_loss': val_loss,
        #           'val_auroc': auroc, 'val_auprc': aupr})

        # print("step", t)
        # print("aupr", aupr)
        # print("auroc", auroc)

        net.train()
        # val_loss /= len(valid_loader)

        if t % 1000 == 0:
            print("        Epoch:{} Loss:{}".format(t, loss.item()))
            # print(f'                Validation loss: {val_loss.item()}')
            print(f"                Validation loss: {val_loss}")

        # GLOBAL_EPOCH += 1

        # check early stopping criteria
        stop = early_stop.check_criteria(net, t, val_loss, aupr, auroc)
        if stop:
            # wandb.log({"generation": GLOBAL_EPOCH,'best_val_loss': early_stop.best_val, 'best_val_auroc': auroc, 'best_val_auprc': aupr})
            net = early_stop.restore_best(net)
            break

    return net


def create_and_train_network(
    smi_list: List[str],
    targets: np.array,
    n_hidden: List[int] = [100, 10],
    use_gpu: bool = True,
    num_workers: int = 1,
):
    """Featurize smiles and train classifier network. Return trained network."""
    # featurize
    dataset_x = obtain_features(smi_list, num_workers=num_workers)
    dataset_y = np.array(targets)
    # if not use_lm:
    avg_val = np.percentile(targets, 80)  # np.average(targets)
    dataset_y = np.expand_dims([1 if x >= avg_val else 0 for x in targets], -1)

    # create network
    device = get_device(use_gpu)
    net, optimizer, loss_func = create_network(
        n_hidden, dataset_x.shape[-1], dataset_y.shape[-1], device
    )

    # train network
    # Print trainable parameters

    total_params = 0

    for name, param in net.named_parameters():
        if param.requires_grad:
            print(
                f"Parameter Name: {name}, Size: {param.size()}, Number of elements: {param.numel()}"
            )
            total_params += param.numel()

    print(f"Total number of trainable parameters: {total_params}")

    net = do_x_training_steps(
        data_x=dataset_x,
        data_y=dataset_y,
        net=net,
        optimizer=optimizer,
        loss_func=loss_func,
        steps=20000,
        batch_size=1024,
        device=device,
        use_lm=False,
    )

    return net


def obtain_model_pred(
    smi_list: List[str],
    net: nn.Module,
    use_gpu: bool = True,
    num_workers: int = 1,
    batch_size: int = 1024,
    use_lm: bool = False,
):
    predictions = []

    device = get_device(use_gpu)
    if use_lm:
        data_x = smi_list
        loader = DataLoader(data_x, batch_size=batch_size)
    else:
        data_x = obtain_features(smi_list, num_workers=num_workers)
        data_x = torch.tensor(data_x, device=device, dtype=torch.float)
        loader = DataLoader(TensorDataset(data_x), batch_size=batch_size)

    num_batches = -(-len(data_x) // batch_size)  # ceil division

    print("Number of batches: ", num_batches)
    with torch.no_grad():
        for i, x in enumerate(loader):
            print("        Predicting Batch: {}/{}".format(i, num_batches))
            if use_lm:
                # outputs = net.forward_batch(x)#.unsqueeze(1)
                outputs = net(x)  # .unsqueeze(1)
            else:
                outputs = net(x[0])
            out_ = outputs.detach().cpu().numpy()
            predictions.append(out_)

    predictions = np.concatenate(predictions, axis=0)  # concatenate in the batch axis
    # predictions = torch.stack(predictions, axis=0)   # concatenate in the batch axis

    return predictions


if __name__ == "__main__":
    # net = MolCLIP(prompt="This molecule is lipophilic.")
    # print("loaded model!!")
    smi = [
        "CCCCCCCCCCCCCCCCOP(=O)(O)Oc1ccc(C=Cc2ccc(OP(=O)(O)OCCCCCCCCCCCCCCCC)cc2)cc1",
        "O=C(CCCCCCCCCCCCC1CCCC1)Nc1ccc(S(=O)(=O)c2ccc(NC(=O)CCCCCCCCCCCCC3CCCC3)cc2)cc1",
        "c1ccc(-c2nc(-c3ccccc3)n(CCCCCCn3c(-c4ccccc4)nc(-c4ccccc4)c3-c3ccccc3)c2-c2ccccc2)cc1",
        "CCCCCCCCCCCCCCCC(=O)Oc1cc(S(=O)(=O)O)cc2cc(S(=O)(=O)O)cc(OC(=O)CCCCCCCCCCCCCCC)c12",
        "Cc1ccc(CN2CCC(=Cc3ccc(C(=O)NC(C)c4ccc(Br)cc4)cc3)CC2)c2ccccc12",
        "CC1CCC2C(C)C(CC(COP(=O)(O)Oc3ccccc3)CC3OC4OC5(C)CCC6C(C)CCC(C3C)C46OO5)OC3OC4(C)CCC1C32OO4",
        "CC(=O)OCC12C=CC(C(C)C)=C1C1CCC3C4(C)CCC(OC(C)=O)C(C)(C)C4CCC3(C)C1(C)CC2",
        "Cc1c(C(O)=Nc2cccc(C(F)(F)F)c2)cc(-c2ccccc2)n1CCCN(C)C(=O)c1ccc2occc2c1",
    ]

    targets = [15.8112, 14.1392, 12.3422, 12.1066, 8.08112, 8.0798, 8.0589, 8.02712]
    print("targets", targets)

    net = create_and_train_network(
        smi,
        targets,
        use_gpu=True,
        use_lm=True,
    )
    print(smi)
    print("targets", targets)
    preds = obtain_model_pred(smi, net, use_lm=True)
    print(preds)
    output = torch.sigmoid(preds)
    print(output)
