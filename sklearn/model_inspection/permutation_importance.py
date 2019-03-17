"""Permutation importance for estimators"""
import numpy as np

from ..utils import check_random_state
from ..metrics import check_scoring


def permutation_importance(estimator, X, y, scoring=None, n_rounds=1,
                           random_state=None):
    """Permutation importance for feature evaluation. [BRE]_

    The permutation importance of a feature is calculated as follows. First,
    the estimator is trained on a training set. Then a baseline metric, defined
    by ``scoring``, is evaluated on a validation set. Next, a feature column
    from the validation set is permuted and the metric is evaluated again.
    The permutation importance is defined to be the difference between the
    baseline metric and metric from permutating the feature column.

    Read more in the :ref:`User Guide <permutation_importance>`.

    Parameters
    ----------
    estimator : object
        A estimator that has already been `fit` and is compatible with
        ``scorer``.

    X : array-like or DataFrame, shape = (n_samples, n_features)
        Training data.

    y : array-like, shape = (n_samples, ...)
        Targets for supervised learning.

    scoring : string, callable or None, optional (default=None)
        A string (see model evaluation documentation) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.

    n_rounds : int, optional (default=1)
        Number of times to permute a feature.

    random_state : int, RandomState instance or None, optional, default None
        The seed of the pseudo random number generator that selects a random
        feature to update.  If int, random_state is the seed used by the random
        number generator; If RandomState instance, random_state is the random
        number generator; If None, the random number generator is the
        RandomState instance used by `np.random`.

    Returns
    -------
    scores : array, shape (n_features, bootstrap_samples)
        Permutation importance scores.

    References
    ----------
    .. [BRE] Breiman, L. Machine Learning (2001) 45: 5.
        https://doi.org/10.1023/A:1010933404324
    """

    random_state = check_random_state(random_state)
    scoring = check_scoring(estimator, scoring=scoring)
    scores = np.empty(shape=(X.shape[1], n_rounds), dtype=np.float)

    # Makes copy since columns will be shuffled
    X = X.copy()

    if hasattr(X, 'iloc'):
        X_iloc = X.iloc
    else:
        X_iloc = X

    baseline_score = scoring(estimator, X, y)
    for col in range(X.shape[1]):
        original_feature = X_iloc[:, col].copy()

        for b_idx in range(n_rounds):
            X_perm = random_state.permutation(original_feature)
            X_iloc[:, col] = X_perm

            feature_score = scoring(estimator, X, y)
            scores[col, b_idx] = baseline_score - feature_score

        X_iloc[:, col] = original_feature

    return scores
