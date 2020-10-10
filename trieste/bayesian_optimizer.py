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
"""
This module contains the :class:`BayesianOptimizer` class, used to perform Bayesian optimization.
"""
from __future__ import annotations

import copy
import traceback
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Generic, Tuple, TypeVar, cast

from absl import logging
import gpflow
import tensorflow as tf

from .acquisition.rule import (
    AcquisitionRule,
    SerialAcquisitionRule,
    EfficientGlobalOptimization,
)
from .datasets import Dataset
from .models import ModelInterface, create_model_interface, ModelSpec
from .observer import Observer
from .space import SearchSpace

S = TypeVar("S")
""" Unbound type variable. """

SP = TypeVar("SP", bound=SearchSpace)
""" Type variable bound to :class:`SearchSpace`. """


class SerialBayesianOptimizer(Generic[SP]):
    """
    This class performs Bayesian optimization, the data efficient optimization of an expensive
    black-box *objective function* over some *search space*. Since we may not have access to the
    objective function itself, we speak instead of an *observer* that observes it.
    """
    @dataclass(frozen=True)
    class LoggingState(Generic[S]):
        """
        Container used to track the state of the optimization process in :class:`BayesianOptimizer`.
        """

        datasets: Mapping[str, Dataset]
        models: Mapping[str, ModelInterface]
        acquisition_state: Optional[S]

    @dataclass(frozen=True)
    class Result(Generic[S]):
        """ Container for the result of the optimization process in :class:`BayesianOptimizer`. """

        datasets: Mapping[str, Dataset]
        models: Mapping[str, ModelInterface]
        error: Optional[Exception]

    def __init__(self, observer: Observer, search_space: SP):
        """
        :param observer: The observer of the objective function.
        :param search_space: The space over which to search. Must be a
            :class:`~trieste.space.SearchSpace`.
        """
        self.observer = observer
        self.search_space = search_space

    def optimize(
        self,
        num_steps: int,
        # note the transforms, datasets and model_specs are kept as separate dicts rather than
        # merged into one dict as that was the style strongly preferred by the researcher we
        # asked at the time
        datasets: Mapping[str, Dataset],
        model_specs: Mapping[str, ModelSpec],
        acquisition_rule: SerialAcquisitionRule[S, SP],
        acquisition_state: Optional[S] = None,
        track_state: bool = True,
    ) -> Tuple[Result[S], List[LoggingState[S]]]:
        """
        Attempt to find the minimizer of the ``observer`` in the ``search_space`` (both specified at
        :meth:`__init__`). This is the central implementation of the Bayesian optimization loop.

        For each step in ``num_steps``, this method:
            - Finds the next points with which to query the ``observer`` using the
              ``acquisition_rule``'s :meth:`acquire` method, passing it the ``search_space``,
              ``datasets`` and models built from the ``model_specs``.
            - Queries the ``observer`` *once* at those points.
            - Updates the datasets and models with the data from the ``observer``.

        Within the optimization loop, this method will catch any errors raised and return them
        instead, along with the latest data, models, and the history of the optimization process.
        This enables the caller to restart the optimization loop from the latest successful step.
        **Note that if an error occurred, the latest data and models might not be from the
        ``num_steps``-th optimization step, but from the step where the error occurred. It is up to
        the caller to check if this has happened, by checking if the result's `error` attribute is
        populated.** Any errors encountered within this method, but outside the optimization loop,
        will be raised as normal. These are documented below.

        **Type hints:**
            - The ``acquisition_rule`` must use the same type of
              :class:`~trieste.space.SearchSpace` as specified in :meth:`__init__`.
            - The history, if populated, will contain an acquisition state of the same type as used
              by the ``acquisition_rule``.

        :param num_steps: The number of optimization steps to run.
        :param datasets: The known observer query points and observations for each tag.
        :param model_specs: The model to use for each :class:`~trieste.datasets.Dataset` (matched
            by tag).
        :param acquisition_rule: The acquisition rule, which defines how to search for a new point
            on each optimization step. Defaults to
            :class:`~trieste.acquisition.rule.EfficientGlobalOptimization` with default
            arguments. Note that if the default is used, this implies the tags must be
            `OBJECTIVE`, the search space can be any :class:`~trieste.space.SearchSpace`, and the
            acquisition state returned in the :class:`OptimizationResult` will be `None`.
        :param acquisition_state: The acquisition state to use on the first optimization step.
            This argument allows the caller to restore the optimization process from a previous
            :class:`LoggingState`.
        :param track_state: If `True`, this method saves the optimization state at the start of each
            step.
        :return: The updated models, data, history containing information from every optimization
            step (see ``track_state``), and the error if any error was encountered during
            optimization.
        :raise ValueError: If any of the following are true:
            - the keys in ``datasets`` and ``model_specs`` do not match
            - ``datasets`` or ``model_specs`` are empty
            - the default `acquisition_rule` is used and the tags are not `OBJECTIVE`.
        """
        if datasets.keys() != model_specs.keys():
            raise ValueError(
                f"datasets and model_specs should contain the same keys. Got {datasets.keys()} and"
                f" {model_specs.keys()} respectively."
            )

        if not datasets:
            raise ValueError("dicts of datasets and model_specs must be populated.")

        models = {tag: create_model_interface(spec) for tag, spec in model_specs.items()}
        history: List[SerialBayesianOptimizer.LoggingState[S]] = []

        for step in range(num_steps):
            try:
                if track_state:
                    history.append(self.LoggingState(
                        datasets, gpflow.utilities.deepcopy(models), copy.deepcopy(acquisition_state)
                    ))

                query_points, acquisition_state = acquisition_rule.acquire(
                    self.search_space, datasets, models, acquisition_state
                )

                observer_output = self.observer(query_points)

                datasets = {tag: datasets[tag] + observer_output[tag] for tag in observer_output}

                for tag, model in models.items():
                    model.update(datasets[tag])
                    model.optimize()

            except Exception as error:
                _log_failure(step)
                return self.Result(datasets, models, error), history

        return self.Result(datasets, models, None), history


class BayesianOptimizer(Generic[SP]):
    @dataclass(frozen=True)
    class LoggingState(Generic[S]):
        dataset: Dataset
        model: ModelInterface
        acquisition_state: Optional[S]

    @dataclass(frozen=True)
    class Result:
        dataset: Dataset
        model: ModelInterface
        error: Optional[Exception]

    def __init__(self, observer: Callable[[tf.Tensor], tf.Tensor], search_space: SP):
        self._observer = observer
        self._search_space = search_space

    def optimize(
        self,
        num_steps: int,
        dataset: Dataset,
        model_spec: ModelSpec,
        acquisition_rule: Optional[AcquisitionRule[S, SP]] = None,
        acquisition_state: Optional[S] = None,
        track_state: bool = True,
    ) -> Tuple[Result, List[LoggingState[S]]]:
        if acquisition_rule is None:
            if acquisition_state is not None:
                raise TypeError

            rule = cast(AcquisitionRule[S, SP], EfficientGlobalOptimization())
        else:
            rule = acquisition_rule

        model = create_model_interface(model_spec)
        history: List[BayesianOptimizer.LoggingState[S]] = []

        for step in range(num_steps):
            try:
                if track_state:
                    history.append(self.LoggingState(
                        dataset, gpflow.utilities.deepcopy(model), copy.deepcopy(acquisition_state)
                    ))

                query_points, acquisition_state = rule.acquire(
                    self._search_space, dataset, model, acquisition_state
                )

                dataset += Dataset(query_points, self._observer(query_points))
                model.update(dataset)
                model.optimize()

            except Exception as error:
                _log_failure(step)
                return self.Result(dataset, model, error), history

        return self.Result(dataset, model, None), history


def _log_failure(step: int) -> None:
    tf.print(
        f"Optimization failed at step {step}, encountered error with traceback:"
        f"\n{traceback.format_exc()}"
        f"\nAborting process and returning results",
        output_stream=logging.ERROR,
    )
