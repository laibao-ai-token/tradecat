import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestETFProfiles(unittest.TestCase):
    def test_auto_driving_profile_defaults(self) -> None:
        from src.etf_profiles import get_etf_domain_profile

        profile = get_etf_domain_profile("auto_driving_cn")
        self.assertEqual(profile.key, "auto_driving_cn")
        self.assertEqual(profile.top_n, 5)
        self.assertEqual(profile.rebalance, "daily")
        self.assertEqual(profile.risk_profile, "conservative")
        self.assertGreaterEqual(len(profile.symbols), 8)

    def test_unknown_profile_falls_back_to_auto_driving(self) -> None:
        from src.etf_profiles import get_etf_domain_profile

        profile = get_etf_domain_profile("unknown-profile")
        self.assertEqual(profile.key, "auto_driving_cn")

    def test_dynamic_loader_prefers_cybercab_chain_weight(self) -> None:
        from src.etf_profiles import load_dynamic_auto_driving_symbols

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "artifacts" / "analysis" / "cybercab_fund_relevance_expanded_20260219.csv"
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text(
                "\n".join(
                    [
                        "code,core_hit,oem_hit,chip_hit,relevance",
                        "516520,2,0,0,90",
                        "024389,2,1,1,80",
                        "159995,1,2,3,95",
                    ]
                ),
                encoding="utf-8",
            )

            symbols = load_dynamic_auto_driving_symbols(root, top_n=3)
            self.assertEqual(symbols, ["024389", "SZ159995", "SH516520"])


if __name__ == "__main__":
    unittest.main()
