"""
Several utilities to reduce clutter in my VPG and TRPO codes.

(c) April 2017 (mostly...) by Daniel Seita
"""

import numpy as np
import tensorflow as tf
import scipy.signal
import sys


def linesearch(f, x, fullstep, expected_improve_rate, max_backtracks=10, accept_ratio=.1):
    """ Backtracking linesearch, from John Schulman's code.

    TODO
    
    Params:
        f:
        x:
        fullstep:
        expected_improve_rate: the slope dy/dx at the initial point
        max_backtracks:
        accept_ratio:

    Returns:
        ...
    """
    fval = f(x)
    print("fval before {}".format(fval))
    for (_n_backtracks, stepfrac) in enumerate(.5**np.arange(max_backtracks)):
        xnew = x + stepfrac*fullstep
        newfval = f(xnew)
        actual_improve = fval - newfval
        expected_improve = expected_improve_rate*stepfrac
        ratio = actual_improve/expected_improve
        print("a/e/r = {}/{}/{}".format(actual_improve, expected_improve, ratio))
        if ratio > accept_ratio and actual_improve > 0:
            print("fval after {}".format(newfval))
            return True, xnew
    return False, x


def cg(f_Ax, b, cg_iters=10, callback=None, verbose=False, residual_tol=1e-10):
    """ Conjugate gradient, from John Schulman's code. 
    
    Sculman used Demmel's book on applied linear algebra, page 312. Fortunately
    I have a copy of it!! Shewchuk also has a version of this in his paper.
    However, Shewchuk emphasizes that this is most useful for *sparse* matrices
    `A`. Is that the case here? We do have a *large* matrix since the number of
    rows/columns is equal to the number of neural network parameters, but is it
    sparse?

    This is used for solving linear systems of `Ax = b`, or `x = A^{-1}b`. In
    TRPO, we don't want to compute `A` (let alone its inverse).  In addition,
    `b` is our usual policy gradient. The goal is to find `A^{-1}b` and then
    later (outside this code) scale that by `alpha`, and then we get the update
    at last. I *think* the alpha-scaling comes from the line search, but I'm not
    sure yet.

    Params:
        f_Ax: A function designed to mimic A*(input). However, we *don't* have
            the entire matrix A formed.
        b: A known vector. In TRPO, it's the vanilla policy gradient (I think).
        cg_iters: Number of iterations of CG.
        callback: (An artifact of John Schulman's code, TODO delete this?)
        verbose: Print extra information for debugging.
        residual_tol: Exit CG if ||r||_2^2 is small enough.

    Returns:
        Our estimate of `A^{-1}b` where A is (approximately?) the Hessian of the
        KL divergence and `b` is given to us.
    """
    p = b.copy()
    r = b.copy()
    x = np.zeros_like(b)
    rdotr = r.dot(r)

    fmtstr =  "%10i %10.3g %10.3g"
    titlestr =  "%10s %10s %10s"
    if verbose: print titlestr % ("iter", "residual norm", "soln norm")

    for i in xrange(cg_iters):
        if callback is not None:
            callback(x)
        if verbose: print fmtstr % (i, rdotr, np.linalg.norm(x))
        z = f_Ax(p)
        v = rdotr / p.dot(z)
        x += v*p
        r -= v*z
        newrdotr = r.dot(r)
        mu = newrdotr/rdotr
        p = r + mu*p
        rdotr = newrdotr
        if rdotr < residual_tol:
            break
    if callback is not None:
        callback(x)
    if verbose: print fmtstr % (i+1, rdotr, np.linalg.norm(x))  # pylint: disable=W0631
    return x


def gauss_log_prob(mu, logstd, x):
    """ Used for computing the log probability, following the formula for the
    multivariate Gaussian density. 
    
    All the inputs should have shape (n,a). The `gp_na` contains component-wise
    probabilitiles, then the reduce_sum results in a tensor of size (n,) which
    contains the log probability for each of the n elements. (We later perform a
    mean on this.) Also, the 2*pi part needs 1/2, but doesn't need the sum over
    the number of components (# of actions) because of the reduce sum here.
    Finally, logstd doesn't need a 1/2 constant because log(\sigma_i^2) will
    bring the 2 over. 
    
    This formula generalizes for an arbitrary number of actions, BUT it assumes
    that the covariance matrix is diagonal.
    """
    var_na = tf.exp(2*logstd)
    gp_na = -tf.square(x - mu)/(2*var_na) - 0.5*tf.log(tf.constant(2*np.pi)) - logstd
    return tf.reduce_sum(gp_na, axis=[1])


def gauss_KL(mu1, logstd1, mu2, logstd2):
    """ Returns KL divergence among two multivariate Gaussians, component-wise.

    It assumes the covariance matrix is diagonal. All inputs have shape (n,a).
    It is not necessary to know the number of actions because reduce_sum will
    sum over this to get the `d` constant offset. The part consisting of the
    trace in the formula is blended with the mean difference squared due to the
    common "denominator" of var2_na.  This forumula generalizes for an arbitrary
    number of actions.  I think mu2 and logstd2 should represent the policy
    before the update.

    Returns the KL divergence for each of the n components in the minibatch,
    then we do a reduce_mean outside this.
    """
    var1_na = tf.exp(2*logstd1)
    var2_na = tf.exp(2*logstd2)
    kl_n = tf.reduce_sum(0.5 * (logstd2 - logstd1 + (var1_na + tf.square(mu1-mu2))/var2_na - 1),
                         axis=[1]) 
    # This assertion sometimes fails. Maybe due to discretization errors?
    #assert_op = tf.Assert(tf.reduce_all(kl_n > -0.001), [kl_n]) 
    #with tf.control_dependencies([assert_op]):
    #    kl_n = tf.identity(kl_n)
    return kl_n


def normc_initializer(std=1.0):
    """ Initialize array with normalized columns """
    def _initializer(shape, dtype=None, partition_info=None): #pylint: disable=W0613
        out = np.random.randn(*shape).astype(np.float32)
        out *= std / np.sqrt(np.square(out).sum(axis=0, keepdims=True))
        return tf.constant(out)
    return _initializer


def dense(x, size, name, weight_init=None):
    """ Dense (fully connected) layer """
    w = tf.get_variable(name + "/w", [x.get_shape()[1], size], initializer=weight_init)
    b = tf.get_variable(name + "/b", [size], initializer=tf.zeros_initializer())
    return tf.matmul(x, w) + b


def fancy_slice_2d(X, inds0, inds1):
    """ Like numpy's X[inds0, inds1] """
    inds0 = tf.cast(inds0, tf.int64)
    inds1 = tf.cast(inds1, tf.int64)
    shape = tf.cast(tf.shape(X), tf.int64)
    ncols = shape[1]
    Xflat = tf.reshape(X, [-1])
    return tf.gather(Xflat, inds0 * ncols + inds1)


def discount(x, gamma):
    """
    Compute discounted sum of future values. Returns a list, NOT a scalar!
    out[i] = in[i] + gamma * in[i+1] + gamma^2 * in[i+2] + ...
    """
    return scipy.signal.lfilter([1],[1,-gamma],x[::-1], axis=0)[::-1]


def lrelu(x, leak=0.2):
    """ Performs a leaky ReLU operation. """
    f1 = 0.5 * (1 + leak)
    f2 = 0.5 * (1 - leak)
    return f1 * x + f2 * abs(x)


def explained_variance_1d(ypred,y):
    """
    Var[ypred - y] / var[y]. 
    https://www.quora.com/What-is-the-meaning-proportion-of-variance-explained-in-linear-regression
    """
    assert y.ndim == 1 and ypred.ndim == 1    
    vary = np.var(y)
    return np.nan if vary==0 else 1 - np.var(y-ypred)/vary


def categorical_sample_logits(logits):
    """
    Samples (symbolically) from categorical distribution, where logits is a NxK
    matrix specifying N categorical distributions with K categories

    specifically, exp(logits) / sum( exp(logits), axis=1 ) is the 
    probabilities of the different classes

    Cleverly uses gumbell trick, based on
    https://github.com/tensorflow/tensorflow/issues/456
    """
    U = tf.random_uniform(tf.shape(logits))
    return tf.argmax(logits - tf.log(-tf.log(U)), dimension=1)


def pathlength(path):
    return len(path["reward"])
