"""Partial dependence plots for regression and classification models. """

# Authors: Peter Prettenhofer
#          Trevor Stephens
# License: BSD 3 clause

from itertools import count
import numbers

import numpy as np
from scipy.stats.mstats import mquantiles

from .utils.extmath import cartesian
from .externals.joblib import Parallel, delayed
from .externals import six
from .externals.six.moves import map, range, zip
from .utils import check_array
from .tree._tree import DTYPE

from .ensemble._gradient_boosting import _partial_dependence_tree
from .ensemble.gradient_boosting import BaseGradientBoosting
from .ensemble.forest import ForestRegressor
from .exceptions import NotFittedError


__all__ = ['partial_dependence', 'plot_partial_dependence']


def _grid_from_X(X, percentiles=(0.05, 0.95), grid_resolution=100):
    """Generate a grid of points based on the ``percentiles of ``X``.

    The grid is generated by placing ``grid_resolution`` equally
    spaced points between the ``percentiles`` of each column
    of ``X``.

    Parameters
    ----------
    X : ndarray
        The data
    percentiles : tuple of floats
        The percentiles which are used to construct the extreme
        values of the grid axes.
    grid_resolution : int
        The number of equally spaced points that are placed
        on the grid.

    Returns
    -------
    grid : ndarray
        All data points on the grid; ``grid.shape[1] == X.shape[1]``
        and ``grid.shape[0] == grid_resolution * X.shape[1]``.
    axes : seq of ndarray
        The axes with which the grid has been created.
    """
    if len(percentiles) != 2:
        raise ValueError('percentile must be tuple of len 2')
    if not all(0. <= x <= 1. for x in percentiles):
        raise ValueError('percentile values must be in [0, 1]')

    axes = []
    emp_percentiles = mquantiles(X, prob=percentiles, axis=0)
    for col in range(X.shape[1]):
        uniques = np.unique(X[:, col])
        if uniques.shape[0] < grid_resolution:
            # feature has low resolution use unique vals
            axis = uniques
        else:
            # create axis based on percentiles and grid resolution
            axis = np.linspace(emp_percentiles[0, col],
                               emp_percentiles[1, col],
                               num=grid_resolution, endpoint=True)
        axes.append(axis)

    return cartesian(axes), axes


def _exact_partial_dependence(est, target_variables, grid, X, output=None):
    """Calculate the partial dependence of ``target_variables``.

    The function will be calculated by calling the ``predict_proba`` method of
    ``est`` for classification or ``predict`` for regression on ``X`` for every
    point in the grid.

    Parameters
    ----------
    est : BaseEstimator
        A fitted classification or regression model.
    target_variables : array-like, dtype=int
        The target features for which the partial dependency should be
        computed (size should be smaller than 3 for visual renderings).
    grid : array-like, shape=(n_points, len(target_variables))
        The grid of ``target_variables`` values for which the
        partial dependency should be evaluated (either ``grid`` or ``X``
        must be specified).
    X : array-like, shape=(n_samples, n_features)
        The data on which ``est`` was trained.
    output : int, optional (default=None)
        The output index to use for multi-output estimators.

    Returns
    -------
    pdp : array, shape=(n_classes, n_points)
        The partial dependence function evaluated on the ``grid``.
        For regression and binary classification ``n_classes==1``.
    """
    n_samples = X.shape[0]
    pdp = []
    for row in range(grid.shape[0]):
        X_eval = X.copy()
        for i, variable in enumerate(target_variables):
            X_eval[:, variable] = np.repeat(grid[row, i], n_samples)
        if est._estimator_type == 'regressor':
            try:
                pdp_row = est.predict(X_eval)
            except NotFittedError:
                raise ValueError('Call %s.fit before partial_dependence' %
                                 est.__class__.__name__)
            if pdp_row.ndim != 1 and pdp_row.shape[1] != 1:
                # Multi-output
                if not 0 <= output < pdp_row.shape[1]:
                    raise ValueError('Valid output must be specified for '
                                     'multi-output models.')
                pdp_row = pdp_row[:, output]
            pdp.append(np.mean(pdp_row))
        elif est._estimator_type == 'classifier':
            try:
                pdp_row = est.predict_proba(X_eval)
            except NotFittedError:
                raise ValueError('Call %s.fit before partial_dependence' %
                                 est.__class__.__name__)
            if isinstance(pdp_row, list):
                # Multi-output
                if not 0 <= output < len(pdp_row):
                    raise ValueError('Valid output must be specified for '
                                     'multi-output models.')
                pdp_row = pdp_row[output]
            pdp_row = np.log(np.clip(pdp_row, 1e-16, 1))
            pdp_row = np.subtract(pdp_row,
                                  np.mean(pdp_row, 1)[:, np.newaxis])
            pdp.append(np.mean(pdp_row, 0))
        else:
            raise ValueError('est must be a fitted regressor or classifier '
                             'model.')
    pdp = np.array(pdp).transpose()
    if pdp.shape[0] == 2:
        # Binary classification
        pdp = pdp[1, :][np.newaxis]
    elif len(pdp.shape) == 1:
        # Regression
        pdp = pdp[np.newaxis]
    return pdp


def _estimated_partial_dependence(est, target_variables, grid, X, output=None):
    """Calculate the partial dependence of ``target_variables``.

    The function will be calculated by calling the ``predict_proba`` method of
    ``est`` for classification or ``predict`` for regression on the mean of
    ``X``.

    Parameters
    ----------
    est : BaseEstimator
        A fitted classification or regression model.
    target_variables : array-like, dtype=int
        The target features for which the partial dependency should be
        computed (size should be smaller than 3 for visual renderings).
    grid : array-like, shape=(n_points, len(target_variables))
        The grid of ``target_variables`` values for which the
        partial dependency should be evaluated (either ``grid`` or ``X``
        must be specified).
    X : array-like, shape=(n_samples, n_features)
        The data on which ``est`` was trained.
    output : int, optional (default=None)
        The output index to use for multi-output estimators.

    Returns
    -------
    pdp : array, shape=(n_classes, n_points)
        The partial dependence function evaluated on the ``grid``.
        For regression and binary classification ``n_classes==1``.
    """
    n_samples = grid.shape[0]
    X_eval = np.tile(X.mean(0), [n_samples, 1])
    for i, variable in enumerate(target_variables):
        X_eval[:, variable] = grid[:, i]
    if est._estimator_type == 'regressor':
        try:
            pdp = est.predict(X_eval)
        except NotFittedError:
            raise ValueError('Call %s.fit before partial_dependence' %
                             est.__class__.__name__)
        if pdp.ndim != 1 and pdp.shape[1] == 1:
            # Column output
            pdp = pdp.ravel()
        if pdp.ndim != 1 and pdp.shape[1] != 1:
            # Multi-output
            if not 0 <= output < pdp.shape[1]:
                raise ValueError('Valid output must be specified for '
                                 'multi-output models.')
            pdp = pdp[:, output]
        pdp = pdp[np.newaxis]
    elif est._estimator_type == 'classifier':
        try:
            pdp = est.predict_proba(X_eval)
        except NotFittedError:
            raise ValueError('Call %s.fit before partial_dependence' %
                             est.__class__.__name__)
        if isinstance(pdp, list):
            # Multi-output
            if not 0 <= output < len(pdp):
                raise ValueError('Valid output must be specified for '
                                 'multi-output models.')
            pdp = pdp[output]
        pdp = np.log(np.clip(pdp, 1e-16, 1))
        pdp = np.subtract(pdp, np.mean(pdp, 1)[:, np.newaxis])
        pdp = pdp.transpose()
    else:
        raise ValueError('est must be a fitted regressor or classifier model.')
    if pdp.shape[0] == 2:
        # Binary classification
        pdp = pdp[1, :][np.newaxis]
    return pdp


def partial_dependence(est, target_variables, grid=None, X=None, output=None,
                       percentiles=(0.05, 0.95), grid_resolution=100,
                       method=None):
    """Partial dependence of ``target_variables``.

    Partial dependence plots show the dependence between the joint values
    of the ``target_variables`` and the function represented
    by the ``est``.

    Read more in the :ref:`User Guide <partial_dependence>`.

    Parameters
    ----------
    est : BaseEstimator
        A fitted classification or regression model.
    target_variables : array-like, dtype=int
        The target features for which the partial dependency should be
        computed (size should be smaller than 3 for visual renderings).
    grid : array-like, shape=(n_points, len(target_variables))
        The grid of ``target_variables`` values for which the
        partial dependency should be evaluated (either ``grid`` or ``X``
        must be specified).
    X : array-like, shape=(n_samples, n_features)
        The data on which ``est`` was trained. It is used to generate
        a ``grid`` for the ``target_variables``. The ``grid`` comprises
        ``grid_resolution`` equally spaced points between the two
        ``percentiles``.
    output : int, optional (default=None)
        The output index to use for multi-output estimators.
    percentiles : (low, high), default=(0.05, 0.95)
        The lower and upper percentile used to create the extreme values
        for the ``grid``. Only if ``X`` is not None.
    grid_resolution : int, default=100
        The number of equally spaced points on the ``grid``.
    method : {'recursion', 'exact', 'estimated', None}, optional (default=None)
        The method to use to calculate the partial dependence function:

        - If 'recursion', the underlying trees of ``est`` will be recursed to
          calculate the function. Only supported for BaseGradientBoosting and
          ForestRegressor.
        - If 'exact', the function will be calculated by calling the
          ``predict_proba`` method of ``est`` for classification or ``predict``
          for regression on ``X``for every point in the grid. To speed up this
          method, you can use a subset of ``X`` or a more coarse grid.
        - If 'estimated', the function will be calculated by calling the
          ``predict_proba`` method of ``est`` for classification or ``predict``
          for regression on the mean of ``X``.
        - If None, then 'recursion' will be used if ``est`` is
          BaseGradientBoosting or ForestRegressor, and 'exact' used for other
          estimators.

    Returns
    -------
    pdp : array, shape=(n_classes, n_points)
        The partial dependence function evaluated on the ``grid``.
        For regression and binary classification ``n_classes==1``.
    axes : seq of ndarray or None
        The axes with which the grid has been created or None if
        the grid has been given.

    Examples
    --------
    >>> samples = [[0, 0, 2], [1, 0, 0]]
    >>> labels = [0, 1]
    >>> from sklearn.ensemble import GradientBoostingClassifier
    >>> gb = GradientBoostingClassifier(random_state=0).fit(samples, labels)
    >>> kwargs = dict(X=samples, percentiles=(0, 1), grid_resolution=2)
    >>> partial_dependence(gb, [0], **kwargs) # doctest: +SKIP
    (array([[-4.52...,  4.52...]]), [array([ 0.,  1.])])
    """
    if method is None:
        if isinstance(est, (BaseGradientBoosting, ForestRegressor)):
            method = 'recursion'
        else:
            method = 'exact'
    if (not isinstance(est, (BaseGradientBoosting, ForestRegressor)) and
            method == 'recursion'):
        raise ValueError('est has to be an instance of BaseGradientBoosting or'
                         ' ForestRegressor for the "recursion" method. Try '
                         'using method="exact" or "estimated".')
    if (not hasattr(est, '_estimator_type') or
            est._estimator_type not in ('classifier', 'regressor')):
        raise ValueError('est must be a fitted regressor or classifier model.')
    if method != 'recursion' and est._estimator_type == 'classifier':
        raise ValueError('est requires a predict_proba method for '
                         'method="exact" or "estimated" for classification.')
    if method == 'recursion':
        if len(est.estimators_) == 0:
            raise ValueError('Call %s.fit before partial_dependence' %
                             est.__class__.__name__)
        n_features = est.n_features_
    elif X is None:
        raise ValueError('X is required for method="exact" or "estimated".')
    else:
        n_features = X.shape[1]
    if (grid is None and X is None) or (grid is not None and X is not None):
        raise ValueError('Either grid or X must be specified')

    target_variables = np.asarray(target_variables, dtype=np.int32,
                                  order='C').ravel()

    if any([not (0 <= fx < n_features) for fx in target_variables]):
        raise ValueError('target_variables must be in [0, %d]'
                         % (n_features - 1))

    if X is not None:
        X = check_array(X, dtype=DTYPE, order='C')
        grid, axes = _grid_from_X(X[:, target_variables], percentiles,
                                  grid_resolution)
    else:
        assert grid is not None
        # don't return axes if grid is given
        axes = None
        # grid must be 2d
        if grid.ndim == 1:
            grid = grid[:, np.newaxis]
        if grid.ndim != 2:
            raise ValueError('grid must be 2d but is %dd' % grid.ndim)

    grid = np.asarray(grid, dtype=DTYPE, order='C')
    assert grid.shape[1] == target_variables.shape[0]

    if method == 'recursion':
        if isinstance(est, BaseGradientBoosting):
            n_trees_per_stage = est.estimators_.shape[1]
            n_estimators = est.estimators_.shape[0]
            learning_rate = est.learning_rate
        else:
            n_trees_per_stage = 1
            n_estimators = len(est.estimators_)
            learning_rate = 1.
        pdp = np.zeros((n_trees_per_stage, grid.shape[0],), dtype=np.float64,
                       order='C')
        for stage in range(n_estimators):
            for k in range(n_trees_per_stage):
                if isinstance(est, BaseGradientBoosting):
                    tree = est.estimators_[stage, k].tree_
                else:
                    tree = est.estimators_[stage].tree_
                _partial_dependence_tree(tree, grid, target_variables,
                                         learning_rate, pdp[k])
        if isinstance(est, ForestRegressor):
            pdp /= n_estimators
    elif method == 'exact':
        pdp = _exact_partial_dependence(est, target_variables, grid, X, output)
    elif method == 'estimated':
        pdp = _estimated_partial_dependence(est, target_variables, grid, X,
                                            output)
    else:
        raise ValueError('method "%s" is invalid. Use "recursion", "exact", '
                         '"estimated", or None.' % method)

    return pdp, axes


def plot_partial_dependence(est, X, features, feature_names=None,
                            label=None, n_cols=3, grid_resolution=100,
                            method=None, percentiles=(0.05, 0.95), n_jobs=1,
                            verbose=0, ax=None, line_kw=None,
                            contour_kw=None, **fig_kw):
    """Partial dependence plots for ``features``.

    The ``len(features)`` plots are arranged in a grid with ``n_cols``
    columns. Two-way partial dependence plots are plotted as contour
    plots.

    Read more in the :ref:`User Guide <partial_dependence>`.

    Parameters
    ----------
    est : BaseEstimator
        A fitted classification or regression model.
    X : array-like, shape=(n_samples, n_features)
        The data on which ``est`` was trained.
    features : seq of ints, strings, or tuples of ints or strings
        If seq[i] is an int or a tuple with one int value, a one-way
        PDP is created; if seq[i] is a tuple of two ints, a two-way
        PDP is created.
        If feature_names is specified and seq[i] is an int, seq[i]
        must be < len(feature_names).
        If seq[i] is a string, feature_names must be specified, and
        seq[i] must be in feature_names.
    feature_names : seq of str
        Name of each feature; feature_names[i] holds
        the name of the feature with index i.
    label : object
        The class label for which the PDPs should be computed.
        Only if est is a multi-class model. Must be in ``est.classes_``.
    n_cols : int
        The number of columns in the grid plot (default: 3).
    percentiles : (low, high), default=(0.05, 0.95)
        The lower and upper percentile used to create the extreme values
        for the PDP axes.
    grid_resolution : int, default=100
        The number of equally spaced points on the axes.
    method : {'recursion', 'exact', 'estimated', None}, optional (default=None)
        The method to use to calculate the partial dependence function:

        - If 'recursion', the underlying trees of ``est`` will be recursed to
          calculate the function. Only supported for BaseGradientBoosting and
          ForestRegressor.
        - If 'exact', the function will be calculated by calling the
          ``predict_proba`` method of ``est`` for classification or ``predict``
          for regression on ``X``for every point in the grid. To speed up this
          method, you can use a subset of ``X`` or a more coarse grid.
        - If 'estimated', the function will be calculated by calling the
          ``predict_proba`` method of ``est`` for classification or ``predict``
          for regression on the mean of ``X``.
        - If None, then 'recursion' will be used if ``est`` is
          BaseGradientBoosting or ForestRegressor, and 'exact' used for other
          estimators.
    n_jobs : int
        The number of CPUs to use to compute the PDs. -1 means 'all CPUs'.
        Defaults to 1.
    verbose : int
        Verbose output during PD computations. Defaults to 0.
    ax : Matplotlib axis object, default None
        An axis object onto which the plots will be drawn.
    line_kw : dict
        Dict with keywords passed to the ``matplotlib.pyplot.plot`` call.
        For one-way partial dependence plots.
    contour_kw : dict
        Dict with keywords passed to the ``matplotlib.pyplot.plot`` call.
        For two-way partial dependence plots.
    fig_kw : dict
        Dict with keywords passed to the figure() call.
        Note that all keywords not recognized above will be automatically
        included here.

    Returns
    -------
    fig : figure
        The Matplotlib Figure object.
    axs : seq of Axis objects
        A seq of Axis objects, one for each subplot.

    Examples
    --------
    >>> from sklearn.datasets import make_friedman1
    >>> from sklearn.ensemble import GradientBoostingRegressor
    >>> X, y = make_friedman1()
    >>> clf = GradientBoostingRegressor(n_estimators=10).fit(X, y)
    >>> fig, axs = plot_partial_dependence(clf, X, [0, (0, 1)]) #doctest: +SKIP
    ...
    """
    import matplotlib.pyplot as plt
    from matplotlib import transforms
    from matplotlib.ticker import MaxNLocator
    from matplotlib.ticker import ScalarFormatter

    if method is None:
        if isinstance(est, (BaseGradientBoosting, ForestRegressor)):
            method = 'recursion'
        else:
            method = 'exact'
    if (not isinstance(est, (BaseGradientBoosting, ForestRegressor)) and
            method == 'recursion'):
        raise ValueError('est has to be an instance of BaseGradientBoosting or'
                         ' ForestRegressor for the "recursion" method. Try '
                         'using method="exact" or "estimated".')
    if (not hasattr(est, '_estimator_type') or
            est._estimator_type not in ('classifier', 'regressor')):
        raise ValueError('est must be a fitted regressor or classifier model.')
    if method != 'recursion' and est._estimator_type == 'classifier':
        raise ValueError('est requires a predict_proba method for '
                         'method="exact" or "estimated" for classification.')
    if method == 'recursion':
        if len(est.estimators_) == 0:
            raise ValueError('Call %s.fit before partial_dependence' %
                             est.__class__.__name__)
        n_features = est.n_features_
    elif X is None:
        raise ValueError('X is required for method="exact" or "estimated".')
    else:
        n_features = X.shape[1]

    # set label_idx for multi-class GBRT
    if hasattr(est, 'classes_') and np.size(est.classes_) > 2:
        if label is None:
            raise ValueError('label is not given for multi-class PDP')
        label_idx = np.searchsorted(est.classes_, label)
        if est.classes_[label_idx] != label:
            raise ValueError('label %s not in ``gbrt.classes_``' % str(label))
    else:
        # regression and binary classification
        label_idx = 0

    X = check_array(X, dtype=DTYPE, order='C')
    if est.n_features_ != X.shape[1]:
        raise ValueError('X.shape[1] does not match est.n_features_')

    if line_kw is None:
        line_kw = {'color': 'green'}
    if contour_kw is None:
        contour_kw = {}

    # convert feature_names to list
    if feature_names is None:
        # if not feature_names use fx indices as name
        feature_names = [str(i) for i in range(est.n_features_)]
    elif isinstance(feature_names, np.ndarray):
        feature_names = feature_names.tolist()

    def convert_feature(fx):
        if isinstance(fx, six.string_types):
            try:
                fx = feature_names.index(fx)
            except ValueError:
                raise ValueError('Feature %s not in feature_names' % fx)
        return fx

    # convert features into a seq of int tuples
    tmp_features = []
    for fxs in features:
        if isinstance(fxs, (numbers.Integral,) + six.string_types):
            fxs = (fxs,)
        try:
            fxs = np.array([convert_feature(fx) for fx in fxs], dtype=np.int32)
        except TypeError:
            raise ValueError('features must be either int, str, or tuple '
                             'of int/str')
        if not (1 <= np.size(fxs) <= 2):
            raise ValueError('target features must be either one or two')

        tmp_features.append(fxs)

    features = tmp_features

    names = []
    try:
        for fxs in features:
            l = []
            # explicit loop so "i" is bound for exception below
            for i in fxs:
                l.append(feature_names[i])
            names.append(l)
    except IndexError:
        raise ValueError('All entries of features must be less than '
                         'len(feature_names) = {0}, got {1}.'
                         .format(len(feature_names), i))

    # compute PD functions
    pd_result = Parallel(n_jobs=n_jobs, verbose=verbose)(
        delayed(partial_dependence)(est, fxs, X=X, method=method,
                                    grid_resolution=grid_resolution,
                                    percentiles=percentiles)
        for fxs in features)

    # get global min and max values of PD grouped by plot type
    pdp_lim = {}
    for pdp, axes in pd_result:
        min_pd, max_pd = pdp[label_idx].min(), pdp[label_idx].max()
        n_fx = len(axes)
        old_min_pd, old_max_pd = pdp_lim.get(n_fx, (min_pd, max_pd))
        min_pd = min(min_pd, old_min_pd)
        max_pd = max(max_pd, old_max_pd)
        pdp_lim[n_fx] = (min_pd, max_pd)

    # create contour levels for two-way plots
    if 2 in pdp_lim:
        Z_level = np.linspace(*pdp_lim[2], num=8)

    if ax is None:
        fig = plt.figure(**fig_kw)
    else:
        fig = ax.get_figure()
        fig.clear()

    n_cols = min(n_cols, len(features))
    n_rows = int(np.ceil(len(features) / float(n_cols)))
    axs = []
    for i, fx, name, (pdp, axes) in zip(count(), features, names,
                                        pd_result):
        ax = fig.add_subplot(n_rows, n_cols, i + 1)

        if len(axes) == 1:
            ax.plot(axes[0], pdp[label_idx].ravel(), **line_kw)
        else:
            # make contour plot
            assert len(axes) == 2
            XX, YY = np.meshgrid(axes[0], axes[1])
            Z = pdp[label_idx].reshape(list(map(np.size, axes))).T
            CS = ax.contour(XX, YY, Z, levels=Z_level, linewidths=0.5,
                            colors='k')
            ax.contourf(XX, YY, Z, levels=Z_level, vmax=Z_level[-1],
                        vmin=Z_level[0], alpha=0.75, **contour_kw)
            ax.clabel(CS, fmt='%2.2f', colors='k', fontsize=10, inline=True)

        # plot data deciles + axes labels
        deciles = mquantiles(X[:, fx[0]], prob=np.arange(0.1, 1.0, 0.1))
        trans = transforms.blended_transform_factory(ax.transData,
                                                     ax.transAxes)
        ylim = ax.get_ylim()
        ax.vlines(deciles, [0], 0.05, transform=trans, color='k')
        ax.set_xlabel(name[0])
        ax.set_ylim(ylim)

        # prevent x-axis ticks from overlapping
        ax.xaxis.set_major_locator(MaxNLocator(nbins=6, prune='lower'))
        tick_formatter = ScalarFormatter()
        tick_formatter.set_powerlimits((-3, 4))
        ax.xaxis.set_major_formatter(tick_formatter)

        if len(axes) > 1:
            # two-way PDP - y-axis deciles + labels
            deciles = mquantiles(X[:, fx[1]], prob=np.arange(0.1, 1.0, 0.1))
            trans = transforms.blended_transform_factory(ax.transAxes,
                                                         ax.transData)
            xlim = ax.get_xlim()
            ax.hlines(deciles, [0], 0.05, transform=trans, color='k')
            ax.set_ylabel(name[1])
            # hline erases xlim
            ax.set_xlim(xlim)
        else:
            ax.set_ylabel('Partial dependence')

        if len(axes) == 1:
            ax.set_ylim(pdp_lim[1])
        axs.append(ax)

    fig.subplots_adjust(bottom=0.15, top=0.7, left=0.1, right=0.95, wspace=0.4,
                        hspace=0.3)
    return fig, axs
