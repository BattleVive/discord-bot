from __future__ import annotations

from battlevive_gateway.rank import rank_render_model


def test_rank_render_model_preserves_original_rank_thresholds() -> None:
    model = rank_render_model({"name": "Alpha", "mmr": 2000, "rank": "Gold", "wins": 4, "losses": 1})

    assert model == {
        "username": "Alpha", "rank_current": "Gold", "rank_next": "Platinum",
        "mmr_current": 2000, "mmr_required": 3500, "wins": 4, "losses": 1,
    }
