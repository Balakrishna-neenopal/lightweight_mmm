# Copyright 2021 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Media transformations for accounting for lagging or media effects."""

import functools
from typing import Union

import jax
import jax.numpy as jnp


@functools.partial(jax.jit, static_argnums=[0, 1])
def calculate_seasonality(
    number_periods: int,
    degrees: int,
    gamma_seasonality: Union[int, float, jnp.ndarray],
    frequency: int = 52,
) -> jnp.ndarray:
  """Calculates cyclic variation seasonality using Fourier terms.

  For detailed info check:
    https://en.wikipedia.org/wiki/Seasonality#Modeling

  Args:
    number_periods: Number of seasonal periods in the data. Eg. for 1 year of
      seasonal data it will be 52, for 3 years of the same kind 156.
    degrees: Number of degrees to use. Must be greater or equal than 1.
    gamma_seasonality: Factor to multiply to each degree calculation. Shape must
      be aligned with the number of degrees.
    frequency: Frecuency of the seasonality be in computed. By default is 52 for
      weekly data (52 weeks in a year).

  Returns:
    An array with the seasonality values.
  """

  seasonality_range = jnp.expand_dims(a=jnp.arange(number_periods), axis=-1)
  degrees_range = jnp.arange(degrees)
  inner_value = seasonality_range * 2 * jnp.pi * degrees_range / frequency
  season_matrix_sin = jnp.sin(inner_value)
  season_matrix_cos = jnp.cos(inner_value)
  season_matrix = jnp.concatenate([
      jnp.expand_dims(a=season_matrix_sin, axis=-1),
      jnp.expand_dims(a=season_matrix_cos, axis=-1)
  ],
                                  axis=-1)
  return (season_matrix * gamma_seasonality).sum(axis=2).sum(axis=1)


@jax.jit
def adstock(data: jnp.ndarray,
            lag_weight: float = .9,
            normalise: bool = True) -> jnp.ndarray:
  """Calculates the adstock value of a given array.

  To learn more about advertising lag:
  https://en.wikipedia.org/wiki/Advertising_adstock

  Args:
    data: Input array.
    lag_weight: lag_weight effect of the adstock function. Default is 0.9.
    normalise: Whether to normalise the output value. This normalization will
      divide the output values by (1 / (1 - lag_weight)).

  Returns:
    The adstock output of the input array.
  """

  def adstock_internal(prev_adstock: jnp.array,
                       data: jnp.array,
                       lag_weight: float = lag_weight) -> jnp.array:
    adstock_value = prev_adstock * lag_weight + data
    return adstock_value, adstock_value

  _, adstock_values = jax.lax.scan(
      f=adstock_internal, init=data[0, ...], xs=data[1:, ...])
  adstock_values = jnp.concatenate([jnp.array([data[0, ...]]), adstock_values])
  return jax.lax.cond(
      normalise,
      lambda adstock_values: adstock_values / (1. / (1 - lag_weight)),
      lambda adstock_values: adstock_values,
      operand=adstock_values)


@jax.jit
def hill(data: jnp.ndarray, half_max_effective_concentration: jnp.ndarray,
         slope: jnp.ndarray) -> jnp.ndarray:
  """Calculates the hill function for a given array of values.

  Refer to the following link for detailed information on this equation:
    https://en.wikipedia.org/wiki/Hill_equation_(biochemistry)

  Args:
    data: Input data.
    half_max_effective_concentration: ec50 value for the hill function.
    slope: Slope of the hill function.

  Returns:
    The hill values for the respective input data.
  """

  hill_values = 1. / (1 + (data / half_max_effective_concentration)**(-slope))
  return jnp.where(data == 0, jnp.zeros(data.shape), hill_values)


@functools.partial(jax.vmap, in_axes=(1, 1, None))
def carryover_convolve(data: jnp.array, weights: jnp.array,
                       number_lags: int) -> jnp.array:
  """Applies the convolution between the data and the weights for the carryover.

  Args:
    data: Input data.
    weights: Window weights for the carryover.
    number_lags: Number of lags the window has.

  Returns:
    The result values from convolving the data and the weights with padding.
  """
  window = jnp.concatenate([jnp.zeros(number_lags - 1), weights])
  return jax.scipy.signal.convolve(data, window, mode="same") / weights.sum()


@functools.partial(jax.jit, static_argnums=[3])
def carryover(data: jnp.ndarray,
              ad_effect_retention_rate: Union[float, jnp.ndarray] = .5,
              peak_effect_delay: Union[float, jnp.ndarray] = 1.,
              number_lags: int = 13) -> jnp.ndarray:
  """Calculates media carryover.

  More details about this function can be found in:
  https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/46001.pdf

  Args:
    data: Input data.
    ad_effect_retention_rate: Retention rate of the advertisement effect.
      Default is 0.5.
    peak_effect_delay: Delay of the peak effect in the carryover function.
      Default is 1.
    number_lags: Number of lags to include in the carryover calculation. Default
      is 13.

  Returns:
    The carryover values for the given data with the given parameters.
  """
  lags_arange = jnp.repeat(
      jnp.arange(number_lags, dtype=jnp.float32),
      data.shape[1]).reshape(number_lags, data.shape[1])
  weights = ad_effect_retention_rate**((lags_arange - peak_effect_delay)**2)
  return jnp.transpose(carryover_convolve(data, weights, number_lags))