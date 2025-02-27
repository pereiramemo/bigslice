#!/usr/bin/env python
# vim: set fileencoding=utf-8 :
#
# Copyright (C) 2020 Satria A. Kautsar
# Wageningen University & Research
# Bioinformatics Group
"""bigslice.modules.clustering.birch

Perform BIRCH clustering on features,
produce dataframe of centroids

todo: assign reference by datasets
"""

from os import path
from random import randint, seed
from ..utils import store_pickle
from ..data.database import Database
import numpy as np
import pandas as pd
from sklearn.cluster import Birch
from sklearn.metrics import pairwise_distances
from sklearn.preprocessing import normalize


class BirchClustering:
    """ handles clustering analysis of features in a run """

    def __init__(self, properties: dict):
        self.run_id = properties["run_id"]
        self.random_seed = properties["random_seed"]
        self.threshold = properties["threshold"]
        self.clustering_method = properties["method"]
        self.centroids = properties["centroids"]

    def save(self, database: Database, cache_folder: str):
        """commits clustering data"""
        existing = database.select(
            "clustering",
            "WHERE run_id=?",
            parameters=(self.run_id, ),
            props=["id"]
        )
        if existing:
            # for now, this should not get called
            raise Exception("not_implemented")
        else:
            # save to database
            self.id = database.insert(
                "clustering",
                {
                    "run_id": self.run_id,
                    "clustering_method": self.clustering_method,
                    "num_centroids": self.centroids.shape[0],
                    "random_seed": self.random_seed,
                    "threshold": self.threshold
                }
            )

            # save gcf entries
            gcf_ids = []
            for centroid_idx in range(self.centroids.shape[0]):
                gcf_ids.append(database.insert(
                    "gcf",
                    {
                        "id_in_run": centroid_idx + 1,
                        "clustering_id": self.id
                    }
                ))
            self.centroids.index = gcf_ids

            # save gcf features (only cells > 0)
            for gcf_id, hmm_id in self.centroids[
                    self.centroids > 0].stack().index:
                database.insert(
                    "gcf_models",
                    {
                        "gcf_id": gcf_id,
                        "hmm_id": hmm_id,
                        "value": float(self.centroids.at[gcf_id, hmm_id])
                    }
                )

            if cache_folder:
                # save pickled cache for quick membership assignment
                pickled_file_path = path.join(
                    cache_folder, "gcf_models_{}.pkl".format(self.id))
                store_pickle(self.centroids, pickled_file_path)

    @ staticmethod
    def run(run_id: int,
            database: Database,
            cache_folder: str,
            complete_only: bool=True,
            threshold: float=-1,
            threshold_percentile: float=-1,
            random_seed: int=randint(1, 9999999)):
        """ run clustering and returns object """

        def preprocess(features: np.array):
            preprocessed_features = features.astype(float)
            preprocessed_features = preprocessed_features[
                np.argsort(np.sum(preprocessed_features, axis=1)),
                :
            ]
            preprocessed_features = normalize(preprocessed_features, norm="l2", copy=False)
            return preprocessed_features

        def fetch_threshold(df: pd.DataFrame,
                            percentile: float,
                            num_iter: int=100,
                            num_sample: int=1000
                            ):
            seed(random_seed)  # to make things reproducible
            if df.shape[0] < num_sample:
                num_sample = df.shape[0]
                num_iter = 1
            threshold = np.array([np.percentile(
                pairwise_distances(
                    df.sample(
                        num_sample,
                        random_state=randint(0, 999999)
                    ).values,
                    metric='euclidean',
                    n_jobs=-1
                ), percentile) for i in range(num_iter)]

            ).mean()
            return threshold

        # set properties
        properties = {
            "run_id": run_id,
            "random_seed": random_seed,
            "method": "birch"
        }

        # prepare features_df
        hmm_db_id = database.select(
            "run",
            "WHERE run.id=?",
            parameters=(run_id, ),
            props=["run.hmm_db_id"],
            as_tuples=True
        )[0][0]
        hmm_ids = [row[0] for row in database.select(
            "hmm,run",
            "WHERE hmm.db_id=run.hmm_db_id" +
            " AND run.id=?",
            parameters=(run_id, ),
            props=["hmm.id"],
            as_tuples=True
        )]

        # fetch cached feature values
        features_df = pd.read_pickle(
            path.join(
            cache_folder, "bgc_features_{}.pkl".format(hmm_db_id))
        ).reindex(columns=hmm_ids)

        # apply filter
        if complete_only:
            selector = " AND bgc.on_contig_edge is 0"
        else:
            selector = ""
        bgc_ids = [row[0] for row in database.select(
            "bgc,run_bgc_status",
            "WHERE run_bgc_status.run_id=?" +
            " AND run_bgc_status.bgc_id=bgc.id" +
            selector,
            parameters=(run_id, ),
            props=["bgc.id"],
            as_tuples=True
        )]
        if len(bgc_ids) < 1:  # check if no bgc_ids
            raise Exception("Not enough input for clustering.")
        bgc_ids_present = list(features_df.index.values)
        bgc_ids = list(set(bgc_ids_present) & set(bgc_ids))
        features_df = features_df.loc[bgc_ids]

        # initiate birch object
        birch = Birch(
            n_clusters=None,  # no global clustering
            compute_labels=False,  # only calc centroids
            copy=False  # data already copied
        )

        # set threshold
        if threshold >= 0:
            birch.threshold = threshold
        else:
            if threshold_percentile < 0:
                raise Exception("Threshold percentile can't be < 0.00")
            # set threshold based on sampling of features
            birch.threshold = fetch_threshold(
                features_df,
                threshold_percentile
            )
        properties["threshold"] = birch.threshold

        # set flat birch
        birch.branching_factor = features_df.shape[0]

        # call birch
        birch.fit(
            preprocess(
                features_df.values
            )
        )

        # save centroids
        properties["centroids"] = pd.DataFrame(
            birch.subcluster_centers_,
            columns=features_df.columns)

        return BirchClustering(properties)
