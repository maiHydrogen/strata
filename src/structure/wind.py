"""Wind load on a low-rise house to IS 875 (Part 3):2015, as storey forces.

Design wind speed Vz = Vb k1 k2 k3 k4 (cl. 6.3), pressure pz = 0.6 Vz^2 (cl. 7.2),
design pressure pd = Kd Ka Kc pz, not less than 0.7 pz (cl. 7.2). The force on each
storey is pd times the net pressure coefficient times the wall area it collects
(half the storey below and half the storey above, the parapet at the roof). The
frame takes it as joint loads on the windward face, which is easy to check by hand.
"""

K1 = 1.0  # Table 1: general buildings, 50 year design life
K3 = 1.0  # cl. 6.3.3: flat ground (upwind slope under 3 degrees)
K4 = 1.0  # cl. 6.3.4: importance factor for cyclonic regions applies only to the cyclone-prone coast
KD = 0.9  # cl. 7.2.1: wind directionality factor for buildings
KA = 1.0  # Table 4: area averaging factor for tributary areas up to 10 m2 (conservative above)
KC = 0.9  # cl. 7.3.3.13: combination factor for framed buildings (walls and roof together)
# Table 5 external pressure coefficients for walls of rectangular clad buildings: windward
# +0.7 in every row; leeward between -0.2 and -0.3. Internal pressure acts on both walls and cancels.
CP_WINDWARD, CP_LEEWARD = 0.7, -0.3
NET_CP = CP_WINDWARD - CP_LEEWARD

# Table 2: k2 by terrain category at heights up to 10, 15 and 20 m
K2 = {1: (1.05, 1.09, 1.12), 2: (1.00, 1.05, 1.07), 3: (0.91, 0.97, 1.01), 4: (0.80, 0.80, 0.80)}
# Category 3: "terrain with numerous closely spaced obstructions ... towns and industrial areas"
DEFAULT_TERRAIN = 3


def k2(height, terrain=DEFAULT_TERRAIN):
    """Table 2, linear between 10, 15 and 20 m; the 10 m value below 10 m."""
    a, b, c = K2[terrain]
    if height <= 10:
        return a
    if height <= 15:
        return a + (b - a) * (height - 10) / 5
    return b + (c - b) * min(height - 15, 5) / 5


def design_pressure(height, vb, terrain=DEFAULT_TERRAIN):
    """pd in kN/m2 at `height` m above ground."""
    vz = vb * K1 * k2(height, terrain) * K3 * K4
    pz = 0.6 * vz ** 2 / 1000
    return max(KD * KA * KC * pz, 0.7 * pz)


def basis(vb, terrain=DEFAULT_TERRAIN):
    """The assumptions behind the wind forces, for the design notes."""
    return (f"IS 875 (Part 3):2015 wind: Vb {vb:g} m/s, k1 {K1}, k2 terrain category {terrain} (Table 2, assumed), "
            f"k3 {K3}, k4 {K4}; pd = Kd Ka Kc pz with Kd {KD}, Ka {KA}, Kc {KC}; net Cp {NET_CP:g} "
            f"(Table 5: windward +{CP_WINDWARD}, leeward {CP_LEEWARD})")
