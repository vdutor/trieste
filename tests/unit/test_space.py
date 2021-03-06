# Copyright 2020 The Trieste Contributors
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

from typing import Tuple, List

import pytest
import tensorflow as tf

from trieste.space import SearchSpace, DiscreteSearchSpace, Box


def _points_in_2D_search_space() -> tf.Tensor:
    return tf.constant([[-1., .4], [-1., .6], [0., .4], [0., .6], [1., .4], [1., .6]])


@pytest.mark.parametrize('point', list(_points_in_2D_search_space()))
def test_discrete_search_space_contains_all_its_points(point: tf.Tensor) -> None:
    assert point in DiscreteSearchSpace(_points_in_2D_search_space())


@pytest.mark.parametrize('point', [
    tf.constant([-1., -.4]),
    tf.constant([-1., .5]),
    tf.constant([-2., .4]),
    tf.constant([-2., .7])
])
def test_discrete_search_space_does_not_contain_other_points(point: tf.Tensor) -> None:
    assert point not in DiscreteSearchSpace(_points_in_2D_search_space())


def _assert_correct_number_of_unique_constrained_samples(
        num_samples: int,
        search_space: SearchSpace,
        samples: tf.Tensor
) -> None:
    assert all(sample in search_space for sample in samples)
    assert len(samples) == num_samples

    unique_samples = set(tuple(sample.numpy().tolist()) for sample in samples)

    assert len(unique_samples) == len(samples)


@pytest.mark.parametrize('num_samples', [0, 1, 3, 5, 6])
def test_discrete_search_space_sampling(num_samples: int) -> None:
    search_space = DiscreteSearchSpace(_points_in_2D_search_space())
    samples = search_space.sample(num_samples)
    _assert_correct_number_of_unique_constrained_samples(num_samples, search_space, samples)


@pytest.mark.parametrize('num_samples', [7, 8, 10])
def test_discrete_search_space_sampling_raises_when_too_many_samples_are_requested(
        num_samples: int
) -> None:
    search_space = DiscreteSearchSpace(_points_in_2D_search_space())

    with pytest.raises(ValueError, match='samples'):
        search_space.sample(num_samples)


def _pairs_of_different_shapes() -> List[Tuple[Tuple[int, ...], Tuple[int, ...]]]:
    return [
        ((), (1,)),
        ((1,), (1, 2)),
        ((1, 2), (1, 2, 3)),
    ]


@pytest.mark.parametrize('lower_shape, upper_shape', _pairs_of_different_shapes())
def test_box_raises_if_bounds_have_different_shape(
        lower_shape: Tuple[int, ...],
        upper_shape: Tuple[int, ...]
) -> None:
    lower, upper = tf.zeros(lower_shape), tf.ones(upper_shape)

    with pytest.raises(ValueError, match='bound'):
        Box(lower, upper)


@pytest.mark.parametrize('lower_dtype, upper_dtype', [
    (tf.int8, tf.uint16),
    (tf.uint32, tf.float32),
    (tf.float32, tf.float64),
    (tf.float64, tf.bfloat16),
])
def test_box_raises_if_bounds_have_different_dtypes(
        lower_dtype: Tuple[tf.DType, tf.DType],
        upper_dtype: Tuple[tf.DType, tf.DType]
) -> None:
    lower, upper = tf.zeros((1, 2), dtype=lower_dtype), tf.ones((1, 2), dtype=upper_dtype)

    with pytest.raises(TypeError, match='dtype'):
        Box(lower, upper)


@pytest.mark.parametrize('lower, upper', [
    (tf.ones((3,)), tf.ones((3,))),  # all equal
    (tf.ones((3,)) + 1, tf.ones((3,))),  # lower all higher than upper
    (tf.constant([2.3, -.1, 8.]), tf.constant([3., -.2, 8.])),  # one lower higher than upper
    (tf.constant([2.3, -.1, 8.]), tf.constant([3., -.1, 8.]))  # one lower equal to upper
])
def test_box_raises_if_any_lower_bound_is_not_less_than_upper_bound(
        lower: tf.Tensor,
        upper: tf.Tensor
) -> None:
    with pytest.raises(ValueError):
        Box(lower, upper)


@pytest.mark.parametrize('point', [
    tf.constant([-1., 0., -2.]),   # lower bound
    tf.constant([2., 1., -.5]),    # upper bound
    tf.constant([.5, .5, -1.5]),   # approx centre
    tf.constant([-1., 0., -1.9]),  # near the edge
])
def test_box_contains_point(point: tf.Tensor) -> None:
    assert point in Box(tf.constant([-1., 0., -2.]), tf.constant([2., 1., -.5]))


@pytest.mark.parametrize('point', [
    tf.constant([-1.1, 0., -2.]),   # just outside
    tf.constant([-.5, -.5, 1.5]),   # negative of a contained point
    tf.constant([10., -10., 10.]),  # well outside
])
def test_box_does_not_contain_point(point: tf.Tensor) -> None:
    assert point not in Box(tf.constant([-1., 0., -2.]), tf.constant([2., 1., -.5]))


@pytest.mark.parametrize('bound_shape, point_shape', _pairs_of_different_shapes())
def test_box_contains_raises_on_point_of_different_shape(
        bound_shape: Tuple[int],
        point_shape: Tuple[int],
) -> None:
    box = Box(tf.zeros(bound_shape), tf.ones(bound_shape))
    point = tf.zeros(point_shape)

    with pytest.raises(ValueError, match='(bound)|(point)'):
        _ = point in box


@pytest.mark.parametrize('num_samples', [0, 1, 10])
def test_box_sampling(num_samples: int) -> None:
    box = Box(tf.zeros((3,)), tf.ones((3,)))
    samples = box.sample(num_samples)
    _assert_correct_number_of_unique_constrained_samples(num_samples, box, samples)


@pytest.mark.parametrize('num_samples', [0, 1, 10])
def test_box_discretize_returns_search_space_with_only_points_contained_within_box(
        num_samples: int
) -> None:
    box = Box(tf.zeros((3,)), tf.ones((3,)))
    dss = box.discretize(num_samples)

    samples = dss.sample(num_samples)

    assert all(sample in box for sample in samples)


@pytest.mark.parametrize('num_samples', [0, 1, 10])
def test_box_discretize_returns_search_space_with_correct_number_of_points(
        num_samples: int
) -> None:
    box = Box(tf.zeros((3,)), tf.ones((3,)))
    dss = box.discretize(num_samples)

    samples = dss.sample(num_samples)

    assert len(samples) == num_samples

    with pytest.raises(ValueError):
        dss.sample(num_samples + 1)
