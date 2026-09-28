import sys
sys.path.insert(0, r"D:\1-2\MAAOrch\tools")
import stage_capability as sc

print("can_farm('y9Flipped','1-7') =", sc.can_farm("y9Flipped", "1-7"))
print("can_farm('Nobody','1-7')   =", sc.can_farm("Nobody", "1-7"))
d = sc.can_farm_detail("y9Flipped", "1-7")
print("detail keys =", sorted(d)[:8])
print("probe_farm import check:")
import mas_probe_farm as pf
print("  probe_farm imported; has run/decide:", hasattr(pf, "run"), hasattr(pf, "decide"))
import mas_run_profile as rp
print("  run_profile imported; has validate_profile:", hasattr(rp, "validate_profile"))
