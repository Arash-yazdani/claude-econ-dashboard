"""The one place that composes the production data-source stack.

Every entrypoint builds its source here so the chain is identical across the publish path,
the reconciliation path, and the smoke test — and so no new entrypoint can construct a bare
`FredClient()`, which sends gold_spot's synthetic id to FRED and 400s the whole run. That
class of bug has bitten three scripts (run_briefing, backtest, run_daily_eval); this retires it.

Order: FRED backbone -> Treasury same-day yields (nominal + real) -> BLS same-day releases ->
NY Fed EFFR -> Cboe VIX -> Treasury FiscalData daily debt -> EIA Brent spot -> Freddie PMMS ->
BEA GDP -> Census housing starts -> gold spot (V4 P1+P2+P3,
docs/v4-source-upgrades.md). Each decorator intercepts a disjoint set of series ids and passes
everything else through, so the order among them is immaterial; gold is outermost because its id
is synthetic and never hits FRED. Every decorator is never-raise and degrades to the FRED value.
"""

from __future__ import annotations

import os
from typing import Optional

from .bea_client import BeaSource
from .bls_client import BlsSource
from .cboe_client import CboeSource
from .census_client import CensusSource
from .eia_client import EiaSource
from .fiscaldata_client import FiscalDataSource
from .fred_client import FredClient, FredSource
from .gold_client import GoldSpotSource
from .nyfed_client import NyFedSource
from .pmms_client import PmmsSource
from .treasury_client import TreasuryYieldSource

BEA_API_KEY_ENV = "BEA_API_KEY"
BLS_API_KEY_ENV = "BLS_API_KEY"
CENSUS_API_KEY_ENV = "CENSUS_API_KEY"
EIA_API_KEY_ENV = "EIA_API_KEY"
GOLD_API_KEY_ENV = "GOLD_API_KEY"


def build_source(*, gold_api_key: Optional[str] = None,
                 bls_api_key: Optional[str] = None,
                 eia_api_key: Optional[str] = None,
                 bea_api_key: Optional[str] = None,
                 census_api_key: Optional[str] = None) -> FredSource:
    """Compose the full production source stack. Keys default to their env vars; all optional
    (absent -> that decorator degrades to the FRED value, never an error). The V4 P1 sources
    (NY Fed, Cboe, FiscalData, the Treasury real curve) and PMMS (P2) are keyless."""
    if gold_api_key is None:
        gold_api_key = os.environ.get(GOLD_API_KEY_ENV)
    if bls_api_key is None:
        bls_api_key = os.environ.get(BLS_API_KEY_ENV)
    if eia_api_key is None:
        eia_api_key = os.environ.get(EIA_API_KEY_ENV)
    if bea_api_key is None:
        bea_api_key = os.environ.get(BEA_API_KEY_ENV)
    if census_api_key is None:
        census_api_key = os.environ.get(CENSUS_API_KEY_ENV)
    return GoldSpotSource(
        CensusSource(BeaSource(PmmsSource(EiaSource(FiscalDataSource(CboeSource(NyFedSource(
            BlsSource(TreasuryYieldSource(FredClient()), api_key=bls_api_key)))),
            api_key=eia_api_key)), api_key=bea_api_key), api_key=census_api_key),
        api_key=gold_api_key,
    )
