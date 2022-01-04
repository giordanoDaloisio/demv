import numpy as np
import pandas as pd
from collections import defaultdict
from sklearn.model_selection import KFold
from sklearn.metrics import f1_score, accuracy_score, confusion_matrix, zero_one_loss
from copy import deepcopy
from demv import DEMV


from fairlearn.metrics import MetricFrame

import matplotlib.pyplot as plt
import seaborn as sns

# METRICS


def statistical_parity(data_pred: pd.DataFrame, group_condition: dict, label_name: str, positive_label: str):
    '''
    Implementation of Statistical Parity

    Parameters
    ----------
    data_pred : pandas.DataFrame
        DataFrame with predicted label
    group_condition: dict
        Dictionary for the definition of the unprivileged group of the type `{sensitive_var: unpriv_value}`
    label_name: str
        Label name
    positive_label: str
        Positive value of the label

    Returns
    -------
    float : Statistical Parity value
    '''
    query = '&'.join([f'{k}=={v}' for k, v in group_condition.items()])
    label_query = label_name+'=='+str(positive_label)
    unpriv_group_prob = (len(data_pred.query(query + '&' + label_query))
                         / len(data_pred.query(query)))
    priv_group_prob = (len(data_pred.query('~(' + query + ')&' + label_query))
                       / len(data_pred.query('~(' + query+')')))
    return unpriv_group_prob - priv_group_prob


def disparate_impact(data_pred: pd.DataFrame, group_condition: dict, label_name: str, positive_label: str):
    '''
    Implementation of Disparate Impact

    Parameters
    ----------
    data_pred : pandas.DataFrame
        DataFrame with predicted label
    group_condition: dict
        Dictionary for the definition of the unprivileged group of the type `{sensitive_var: unpriv_value}`
    label_name: str
        Label name
    positive_label: str
        Positive value of the label

    Returns
    -------
    float : Disparate Impact value
    '''
    query = '&'.join([f'{k}=={v}' for k, v in group_condition.items()])
    label_query = label_name+'=='+str(positive_label)
    unpriv_group_prob = (len(data_pred.query(query + '&' + label_query))
                         / len(data_pred.query(query)))
    priv_group_prob = (len(data_pred.query('~(' + query + ')&' + label_query))
                       / len(data_pred.query('~(' + query+')')))
    return min(unpriv_group_prob / priv_group_prob, priv_group_prob/unpriv_group_prob) if unpriv_group_prob != 0 else unpriv_group_prob / priv_group_prob


def zero_one_loss_diff(y_true: np.ndarray, y_pred: np.ndarray, sensitive_features: list):
    '''
    Computes Zero One Loss

    Parameters
    ----------
    y_true : np.ndarray
        List of true labels
    y_pred : np.ndarray
        List of predicted values
    sensitive_features : list
        Sensitive variable names

    Returns
    -------
    typing.Any or pandas.Series or pandas.DataFrame
        Difference of the metric among groups
    '''
    mf = MetricFrame(metrics=zero_one_loss,
                     y_true=y_true,
                     y_pred=y_pred,
                     sensitive_features=sensitive_features)
    return mf.difference()

# TRAINING FUNCTIONS


def _train_test_split(df_train, df_test, label):
    x_train = df_train.drop(label, axis=1).values
    y_train = df_train[label].values.ravel()
    x_test = df_test.drop(label, axis=1).values
    y_test = df_test[label].values.ravel()
    return x_train, x_test, y_train, y_test


def cross_val(classifier, data, label, groups_condition, sensitive_features, positive_label, debiaser=None, exp=False, n_splits=10):
    '''
    Evaluation function

    Parameters
    ----------
    classifier : 
        Sklearn classifier
    data : pandas.DataFrame
        Train/Test dataset
    label : string
        Label name
    group_condition : dict
        Dictionary for the definition of the unprivileged group of the type `{sensitive_var: unpriv_value}`
    sensitive_features : list
        List of sensitive attribute names
    positive_label : int
        Positive value of the label
    debiaser : optional
        Debiaser function (default to None)
    exp : bool, optional
        Flag to indicate if use Exp Gradient function (default to False)
    n_splits : int, optional
        Number of train-test split

    Returns
    -------
    sklearn.classifier:
        Trained classifier
    dict:
        Dictionary of selected evaluation metrics
    '''
    fold = KFold(n_splits=n_splits, shuffle=True, random_state=2)
    metrics = {
        'stat_par': [],
        'zero_one_loss': [],
        'disp_imp': [],
        'acc': [],
        'f1': []
    }
    for train, test in fold.split(data):
        data = data.copy()
        df_train = data.iloc[train]
        df_test = data.iloc[test]
        model = deepcopy(classifier)
        if debiaser:
            run_metrics = _demv_training(model, debiaser, groups_condition, label,
                                         df_train, df_test, positive_label, sensitive_features)
        else:
            run_metrics = _model_train(df_train, df_test, label, model, defaultdict(
                list), groups_condition, sensitive_features, positive_label, exp)
        for k in metrics.keys():
            metrics[k].append(run_metrics[k])
    return model, metrics


def eval_demv(k, iters, data, classifier, label, groups, sensitive_features, positive_label=None):
    ris = defaultdict(list)
    for i in range(0, iters+1, k):
        data = data.copy()
        demv = DEMV(1, debug=False, stop=i)
        _, metrics = cross_val(classifier, data, label, groups,
                               sensitive_features, debiaser=demv, positive_label=positive_label)
        #metrics = _compute_mean(metrics)
        ris['stop'].append(i)
        for k, v in metrics.items():
            val = []
            for i in v:
                val.append(np.mean(i))
            ris[k].append(val)
    return ris


def _demv_training(classifier, debiaser, groups_condition, label, df_train, df_test, positive_label, sensitive_features):
    metrics = defaultdict(list)
    for _ in range(30):
        df_copy = df_train.copy()
        data = debiaser.fit_transform(
            df_copy, [keys for keys in groups_condition.keys()], label)
        metrics = _model_train(data, df_test, label, classifier, metrics,
                               groups_condition, sensitive_features, positive_label)
    return metrics


def _model_train(df_train, df_test, label, classifier, metrics, groups_condition, sensitive_features, positive_label, exp=False):
    x_train, x_test, y_train, y_test = _train_test_split(
        df_train, df_test, label)
    model = deepcopy(classifier)
    model.fit(x_train, y_train,
              sensitive_features=df_train[sensitive_features]) if exp else model.fit(x_train, y_train)
    pred = model.predict(x_test)
    df_pred = df_test.copy()
    df_pred[label] = pred
    metrics['stat_par'].append(statistical_parity(
        df_pred, groups_condition, label, positive_label))
    metrics['disp_imp'].append(disparate_impact(
        df_pred, groups_condition, label, positive_label=positive_label))
    metrics['zero_one_loss'].append(zero_one_loss_diff(
        y_true=y_test, y_pred=pred, sensitive_features=df_test[sensitive_features].values))
    metrics['acc'].append(accuracy_score(y_test, pred))
    metrics['f1'].append(f1_score(y_test, pred, average='weighted'))
    return metrics


def print_metrics(metrics):
    print('Statistical parity: ', round(np.mean(
        metrics['stat_par']), 3), ' +- ', round(np.std(metrics['stat_par']), 3))
    print('Disparate impact: ', round(np.mean(
        metrics['disp_imp']), 3), ' +- ', round(np.std(metrics['disp_imp']), 3))
    print('Zero one loss: ', round(np.mean(
        metrics['zero_one_loss']), 3), ' +- ', round(np.std(metrics['zero_one_loss']), 3))
    print('F1 score: ', round(
        np.mean(metrics['f1']), 3), ' +- ', round(np.std(metrics['f1']), 3))
    print('Accuracy score: ', round(np.mean(
        metrics['acc']), 3), ' +- ', round(np.std(metrics['acc']), 3))


# PLOT FUNCTIONS

def plot_group_percentage(data, protected_vars: list, label_name, label_value):
    full_list = protected_vars.copy()
    full_list.append(label_name)
    perc = (data[full_list]
            .groupby(protected_vars)[label_name]
            .value_counts(normalize=True)
            .mul(100).rename('Percentage')
            .reset_index()
            )
    perc['Groups'] = perc[protected_vars].apply(
        lambda x: '('+','.join(x.astype(str))+')', axis=1)
    sns.barplot(data=perc[perc[label_name]
                == label_value], x='Groups', y='Percentage')
    plt.title('Percentage distribution of label for each sensitive group')
    plt.show()


def plot_metrics_curves(df, points, title=''):

    metrics = {'stat_par': 'Statistical Parity', 'zero_one_loss': 'Zero One Loss',
               'disp_imp': 'Disparate Impact', 'acc': 'Accuracy'}
    _, ax = plt.subplots(1, 1, figsize=(10, 8))
    for k, v in metrics.items():
        ax = sns.lineplot(data=df, y=k, x='stop', label=v, ci='sd')
    for k, v in points.items():
        ax.plot(v['x'], v['y'], v['type'], label=k, markersize=10)
    ax.set(ylabel='Value', xlabel='Stop value')
    ax.lines[0].set_linestyle("--")
    ax.lines[0].set_marker('o')
    #lines[1] is zero_one_loss
    ax.lines[1].set_marker('x')
    ax.lines[1].set_markeredgecolor('orange')
    ax.lines[1].set_linestyle("--")

    ax.lines[2].set_marker('+')
    ax.lines[2].set_markeredgecolor('green')
    ax.lines[2].set_linestyle(":")
    ax.lines[2].set_markevery(0.001)

    ax.lines[3].set_color("black")
    ax.legend(handlelength=5, loc="upper center", bbox_to_anchor=(
        0.5, -0.03), ncol=3, fancybox=True, shadow=True)
    plt.title(title)
    plt.tight_layout()
    plt.show()


def plot_grid(dfs, ys, iter, types, metrics):
    fig = plt.figure(dpi=60, tight_layout=True)
    fig.set_size_inches(15, 5, forward=True)

    gs = fig.add_gridspec(1, len(dfs))

    ax = np.zeros(3, dtype=object)

    for k, v in dfs.items():
        df = v
        points = ys[k]
        iters = iter[k]
        i = list(dfs.keys()).index(k)
        ax[i] = fig.add_subplot(gs[0, i])
        for key, v in metrics.items():
            ax[i] = sns.lineplot(data=df, y=key, x='stop', label=v, ci='sd')

        for key, v in points.items():
            ax[i].plot(iters, points[key], types[key],
                       label=key, markersize=10)

        ax[i].set(ylabel='Value', xlabel='Stop value')
        ax[i].set_title(k)

        ax[i].lines[0].set_linestyle("--")
        ax[i].lines[0].set_marker('o')
        #lines[1] is zero_one_loss
        ax[i].lines[1].set_marker('x')
        ax[i].lines[1].set_markeredgecolor('orange')
        ax[i].lines[1].set_linestyle("--")

        ax[i].lines[2].set_marker('+')
        ax[i].lines[2].set_markeredgecolor('green')
        ax[i].lines[2].set_linestyle(":")
        ax[i].lines[2].set_markevery(0.001)
        ax[i].get_legend().remove()
        ax[i].plot()

    handles, labels = ax[len(dfs)-1].get_legend_handles_labels()
    lgd = fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(
        0.5, -0.03), ncol=4, prop={'size': 15}, fancybox=True, shadow=True)
    fig.savefig('img/Grid.pdf', bbox_extra_artists=(lgd,), bbox_inches='tight')


def plot_gridmulti(dfs, ys, iter, types, metrics, name='GridMulti'):
    fig = plt.figure(dpi=60, tight_layout=True)
    fig.set_size_inches(15, 8, forward=True)

    gs = fig.add_gridspec(2, 6)
    ax = np.zeros(5, dtype=object)

    for k, v in dfs.items():

        df = v
        points = ys[k]
        iters = iter[k]
        i = list(dfs.keys()).index(k)

        if(i == 0):
            ax[i] = fig.add_subplot(gs[0, :2])
        elif(i == 1):
            ax[i] = fig.add_subplot(gs[0, 2:4])
        elif(i == 2):
            ax[i] = fig.add_subplot(gs[0, 4:])
        elif(i == 3):
            ax[i] = fig.add_subplot(gs[1, 1:3])
        elif(i == 4):
            ax[i] = fig.add_subplot(gs[1, 3:5])

        for key, v in metrics.items():
            ax[i] = sns.lineplot(data=df, y=key, x='stop', label=v, ci='sd')

        for key, v in points.items():
            ax[i].plot(iters, points[key], types[key],
                       label=key, markersize=10)

        ax[i].set(ylabel='Value', xlabel='Stop value')
        ax[i].set_title(k)

        ax[i].lines[0].set_linestyle("--")
        ax[i].lines[0].set_marker('o')
        #lines[1] is zero_one_loss
        ax[i].lines[1].set_marker('x')
        ax[i].lines[1].set_markeredgecolor('orange')
        ax[i].lines[1].set_linestyle("--")

        ax[i].lines[2].set_marker('+')
        ax[i].lines[2].set_markeredgecolor('green')
        ax[i].lines[2].set_linestyle(":")
        ax[i].lines[2].set_markevery(0.001)
        ax[i].get_legend().remove()
        ax[i].plot()

    handles, labels = ax[len(dfs)-1].get_legend_handles_labels()
    lgd = fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(
        0.5, -0.03), ncol=4, prop={'size': 15}, fancybox=True, shadow=True)
    fig.savefig(f'img/{name}.pdf',
                bbox_extra_artists=(lgd,), bbox_inches='tight')


def preparepoints(metrics, iters):

    types = {'Stastical Parity (Exp Gradient)': 'xb',
             'Zero One Loss (Exp Gradient)': 'xy',
             'Disparate Impact (Exp Gradient)': 'xg',
             'Accuracy (Exp Gradient)': 'xr',
             }

    rename = {'Stastical Parity (Exp Gradient)': 'stat_par',
              'Zero One Loss (Exp Gradient)': 'zero_one_loss',
              'Disparate Impact (Exp Gradient)': 'disp_imp',
              'Accuracy (Exp Gradient)': 'acc'
              }

    points = {}

    for k in types.keys():
        points[k] = {'x': iters, 'y': np.mean(
            metrics[rename[k]]), 'type':  types[k]}

    return points


def unprivpergentage(data, unpriv_group, iters):
    unprivdata = data.copy()
    for k, v in unpriv_group.items():
        unprivdata = unprivdata[(unprivdata[k] == v)]

    xshape, _ = unprivdata.shape

    print('Dataset size:', data.shape[0])
    print('Unprivileged group size:', xshape)
    print('Percentage of unprivileged group:', (xshape/data.shape[0])*100)
    print('Number of iterations:', iters)


def prepareplots(metrics, name):

    df = pd.DataFrame(metrics)
    columnlist = []
    for i in df.columns.values:
        if (i != 'stop'):
            columnlist.append(i)

    df = df.explode(columnlist)

    df.to_csv('ris/'+name+'_eval.csv')

    return df


def gridcomparison(dfs, dfsm, ys, ysm, iter, iterm, types, metrics):

    fig = plt.figure(dpi=60, tight_layout=True)
    fig.set_size_inches(15, 15, forward=True)

    gs = fig.add_gridspec(5, 2)
    ax = np.zeros(10, dtype=object)

    for k, v in dfs.items():
        df = v
        points = ys[k]
        iters = iter[k]
        i = list(dfs.keys()).index(k)

        if(i == 0):
            ax[i] = fig.add_subplot(gs[0, 0])
        elif(i == 1):
            ax[i] = fig.add_subplot(gs[1, 0])
        elif(i == 2):
            ax[i] = fig.add_subplot(gs[2, 0])
        elif(i == 3):
            ax[i] = fig.add_subplot(gs[3, 0])
        elif(i == 4):
            ax[i] = fig.add_subplot(gs[4, 0])

        for key, v in metrics.items():
            ax[i] = sns.lineplot(data=df, y=key, x='stop', label=v, )

        for key, v in points.items():
            ax[i].plot(iters, points[key], types[key],
                       label=key, markersize=10)

        ax[i].set(ylabel='Value', xlabel='Stop value')
        ax[i].set_title(k + " single var")

        ax[i].lines[0].set_linestyle("--")
        ax[i].lines[0].set_marker('o')
        #lines[1] is zero_one_loss
        ax[i].lines[1].set_marker('x')
        ax[i].lines[1].set_markeredgecolor('orange')
        ax[i].lines[1].set_linestyle("--")

        ax[i].lines[2].set_marker('+')
        ax[i].lines[2].set_markeredgecolor('green')
        ax[i].lines[2].set_linestyle(":")
        ax[i].lines[2].set_markevery(0.001)
        ax[i].get_legend().remove()
        ax[i].plot()

    for k, v in dfsm.items():
        df = v
        points = ysm[k]
        iters = iterm[k]
        i = list(dfsm.keys()).index(k)

        if(i == 0):
            ax[i] = fig.add_subplot(gs[0, 1])
        elif(i == 1):
            ax[i] = fig.add_subplot(gs[1, 1])
        elif(i == 2):
            ax[i] = fig.add_subplot(gs[2, 1])
        elif(i == 3):
            ax[i] = fig.add_subplot(gs[3, 1])
        elif(i == 4):
            ax[i] = fig.add_subplot(gs[4, 1])

        for key, v in metrics.items():
            ax[i] = sns.lineplot(data=df, y=key, x='stop', label=v, )

        for key, v in points.items():
            ax[i].plot(iters, points[key], types[key],
                       label=key, markersize=10)

        ax[i].set(ylabel='Value', xlabel='Stop value')
        ax[i].set_title(k)

        ax[i].lines[0].set_linestyle("--")
        ax[i].lines[0].set_marker('o')
        #lines[1] is zero_one_loss
        ax[i].lines[1].set_marker('x')
        ax[i].lines[1].set_markeredgecolor('orange')
        ax[i].lines[1].set_linestyle("--")

        ax[i].lines[2].set_marker('+')
        ax[i].lines[2].set_markeredgecolor('green')
        ax[i].lines[2].set_linestyle(":")
        ax[i].lines[2].set_markevery(0.001)
        ax[i].get_legend().remove()
        ax[i].plot()

    handles, labels = ax[len(dfs)-1].get_legend_handles_labels()
    lgd = fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(
        0.5, -0.03), ncol=4, prop={'size': 15}, fancybox=True, shadow=True)
    fig.savefig('img/GridMultiSingleVar.pdf',
                bbox_extra_artists=(lgd,), bbox_inches='tight')