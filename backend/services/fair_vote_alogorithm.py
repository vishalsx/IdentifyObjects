from motor.motor_asyncio import AsyncIOMotorClient
import numpy as np
import pandas as pd
from scipy.stats import rankdata
import math
from db.connection import translations_collection


# ---------- FAIR STAR CALCULATION ----------
async def calculate_fair_stars_from_mongo():
    """
    Calculates fair weighted star ratings for each object using live MongoDB data.
    """

    # ---- Step 1: Fetch compact data directly aggregated from Mongo ----
    # This avoids transferring all documents and reduces processing cost.
    pipeline = [
        {"$match": {"translation_status": "Approved"}},
        {
            "$project": {
                "object_id": 1,
                "requested_language": 1,
                "net_votes": {
                    "$subtract": [
                        {"$ifNull": ["$up_votes", 0]},
                        {"$ifNull": ["$down_votes", 0]}
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": {"object_id": "$object_id", "language": "$requested_language"},
                "total_votes": {"$sum": "$net_votes"},
            }
        },
        {
            "$group": {
                "_id": "$_id.object_id",
                "votes": {
                    "$push": {
                        "language": "$_id.language",
                        "votes": "$total_votes"
                    }
                }
            }
        }
    ]

    cursor = translations_collection.aggregate(pipeline)
    docs = await cursor.to_list(length=None)
    if not docs:
        print("No approved translations found.")
        return None, None

    # ---- Step 2: Convert into nested {object: {lang: votes}} format ----
    vote_data = {}
    for doc in docs:
        obj_id = str(doc["_id"])
        lang_votes = {v["language"]: v["votes"] for v in doc.get("votes", [])}
        vote_data[obj_id] = lang_votes

    # ---- Step 3: Convert to DataFrame ----
    df = pd.DataFrame(vote_data).T.fillna(0)
    languages = df.columns.tolist()
    objects = df.index.tolist()

    # ---- Step 4: Language-level normalization and weighting ----
    lang_totals = df.sum(axis=0)
    lang_means = df.mean(axis=0)
    lang_stds = df.std(axis=0).replace(0, 1)

    df_normalized = (df - lang_means) / lang_stds

    log_weights = np.log1p(lang_totals)
    normalized_weights = log_weights / log_weights.sum()

    # ---- Step 5: Bayesian smoothing ----
    global_mean = df.stack().mean()
    smoothing_factor = 5
    df_smoothed = (df + smoothing_factor * global_mean) / (1 + smoothing_factor)

    # ---- Step 6: Weighted scoring ----
    weighted_scores = (
        (df_normalized * normalized_weights).sum(axis=1)
        + (df_smoothed.sum(axis=1) / len(languages)) * 0.1
    )

    # ---- Step 7: Star calculation ----
    percent_ranks = rankdata(weighted_scores, method="average") / len(weighted_scores)
    stars = np.ceil(percent_ranks * 5)
    stars = np.maximum(stars, 1).astype(int)

    results = pd.DataFrame({
        "object_id": objects,
        "weighted_score": weighted_scores,
        "percentile_rank": percent_ranks,
        "stars": stars
    }).sort_values(by="weighted_score", ascending=False).reset_index(drop=True)

    meta = {
        "language_weights": dict(zip(languages, normalized_weights.round(3))),
        "language_totals": dict(zip(languages, lang_totals.astype(int))),
        "language_means": dict(zip(languages, lang_means.round(2))),
        "language_stds": dict(zip(languages, lang_stds.round(2))),
    }

    return results, meta
