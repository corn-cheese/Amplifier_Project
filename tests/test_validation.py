import unittest

from langgraph_workflow.validation import validate_seed


VALID_SEED = {
    "seed_id": "seed_0_abcd1234",
    "topology_name": "bjt_bandpass",
    "subckt_name": "bjt_amp",
    "pins": ["VIN", "VREF", "VDD", "GND", "VOUT"],
    "netlist_template": (
        "simulator lang=spectre\n"
        "subckt bjt_amp VIN VREF VDD GND VOUT\n"
        "XQ1 (VOUT VIN GND GND) npn_05v5_W1p00L1p00 mult={q_mult}\n"
        "R1 (VDD VOUT) resistor r={r_bias}\n"
        "C1 (VOUT GND) capacitor c={c_load}\n"
        "ends bjt_amp\n"
    ),
    "param_ranges": {
        "q_mult": {"type": "int", "low": 1, "high": 8},
        "r_bias": {"type": "log_float", "low": 1_000.0, "high": 1_000_000.0},
        "c_load": {"type": "float", "low": 1e-12, "high": 1e-10},
    },
    "initial_params": {"q_mult": 2, "r_bias": 10000.0, "c_load": 1e-11},
    "device_manifest": [
        {
            "name": "Q1",
            "type": "npn",
            "cell": "sky130_fd_pr__npn_05v5",
            "count": "q_mult",
            "include_in_ppa": True,
        },
        {
            "name": "R1",
            "type": "resistor",
            "cell": "sky130_fd_pr__res_high_po_5p73",
            "segments": 100,
            "seg_length": "5.73u",
            "seg_width": "0.35u",
            "include_in_ppa": True,
        },
        {
            "name": "C1",
            "type": "capacitor",
            "cell": "sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield",
            "width": "11.5u",
            "length": "11.7u",
            "multiplier": 1,
            "include_in_ppa": True,
        },
    ],
}


class SeedValidationTests(unittest.TestCase):
    def test_accepts_valid_bjt_seed(self):
        result = validate_seed(VALID_SEED)

        self.assertTrue(result.valid, result.errors)
        self.assertEqual([], result.errors)

    def test_rejects_manifest_cell_names_used_as_spectre_models(self):
        seed = {
            **VALID_SEED,
            "netlist_template": (
                "simulator lang=spectre\n"
                "subckt bjt_amp VIN VREF VDD GND VOUT\n"
                "Q1 VOUT VIN GND GND sky130_fd_pr__npn_05v5 m={q_mult}\n"
                "R1 VDD VOUT sky130_fd_pr__res_high_po_5p73 r={r_bias}\n"
                "C1 VOUT GND sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield c={c_load}\n"
                "ends bjt_amp\n"
            ),
        }

        result = validate_seed(seed)

        self.assertFalse(result.valid)
        self.assertTrue(any("manifest cell" in error for error in result.errors), result.errors)

    def test_pnp_manifest_cell_error_recommends_available_spectre_subckt(self):
        seed = {
            **VALID_SEED,
            "netlist_template": (
                "simulator lang=spectre\n"
                "subckt bjt_amp VIN VREF VDD GND VOUT\n"
                "XQ1 (VOUT VIN GND GND) sky130_fd_pr__pnp_05v5 mult={q_mult}\n"
                "R1 (VDD VOUT) resistor r={r_bias}\n"
                "C1 (VOUT GND) capacitor c={c_load}\n"
                "ends bjt_amp\n"
            ),
        }

        result = validate_seed(seed)

        self.assertFalse(result.valid)
        self.assertTrue(any("use 'pnp_05v5_W0p68L0p68'" in error for error in result.errors), result.errors)

    def test_rejects_opamp_seed(self):
        seed = {
            **VALID_SEED,
            "seed_id": "seed_1_opamp",
            "topology_name": "forbidden_opamp",
            "netlist_template": (
                "simulator lang=spectre\n"
                "subckt bjt_amp VIN VREF VDD GND VOUT\n"
                "XOP VIN VREF VDD GND VOUT opamp gain={gain} freq_unitygain={ft_hz}\n"
                "R1 (VDD VOUT) resistor r={r_bias}\n"
                "ends bjt_amp\n"
            ),
            "param_ranges": {
                "gain": {"type": "float", "low": 10.0, "high": 1000.0},
                "ft_hz": {"type": "log_float", "low": 1e4, "high": 1e8},
                "r_bias": {"type": "log_float", "low": 1_000.0, "high": 1_000_000.0},
            },
            "initial_params": {"gain": 100.0, "ft_hz": 1e6, "r_bias": 10000.0},
            "device_manifest": [
                {
                    "name": "XOP",
                    "type": "opamp",
                    "cell": "opamp",
                    "count": 1,
                    "ft_hz": "ft_hz",
                    "include_in_ppa": True,
                },
                {
                    "name": "R1",
                    "type": "resistor",
                    "cell": "sky130_fd_pr__res_high_po_5p73",
                    "segments": 100,
                    "seg_length": "5.73u",
                    "seg_width": "0.35u",
                    "include_in_ppa": True,
                },
            ],
        }

        result = validate_seed(seed)

        self.assertFalse(result.valid)
        self.assertTrue(any("forbidden token" in error and "opamp" in error for error in result.errors))

    def test_rejects_opamp_hidden_in_seed_metadata(self):
        seed = {
            **VALID_SEED,
            "seed_id": "seed_opamp_hidden",
            "topology_name": "opamp style topology",
        }

        result = validate_seed(seed)

        self.assertFalse(result.valid)
        self.assertTrue(any("seed metadata" in error and "opamp" in error for error in result.errors))

    def test_accepts_project_allowed_diode_seed(self):
        seed = {
            **VALID_SEED,
            "seed_id": "seed_2_diode",
            "topology_name": "bjt_with_clamp_diode",
            "netlist_template": VALID_SEED["netlist_template"]
            + "DCLAMP VOUT VDD sky130_fd_pr__diode_pd2nw_05v5 area={d_area}\n",
            "param_ranges": {
                **VALID_SEED["param_ranges"],
                "d_area": {"type": "float", "low": 1.0, "high": 10.0},
            },
            "initial_params": {**VALID_SEED["initial_params"], "d_area": 2.0},
            "device_manifest": VALID_SEED["device_manifest"]
            + [
                {
                    "name": "DCLAMP",
                    "type": "diode",
                    "cell": "sky130_fd_pr__diode_pd2nw_05v5",
                    "width": "1u",
                    "length": "1u",
                    "multiplier": "d_area",
                    "include_in_ppa": False,
                }
            ],
        }

        result = validate_seed(seed)

        self.assertTrue(result.valid, result.errors)

    def test_rejects_wrong_pin_order(self):
        seed = {**VALID_SEED, "pins": ["GND", "VDD", "VIN", "VOUT", "VREF"]}

        result = validate_seed(seed)

        self.assertFalse(result.valid)
        self.assertIn("pins must equal ['VIN', 'VREF', 'VDD', 'GND', 'VOUT']", result.errors)

    def test_rejects_forbidden_behavioral_tokens(self):
        seed = {
            **VALID_SEED,
            "netlist_template": VALID_SEED["netlist_template"] + "B1 VOUT GND bsource v=laplace(V(VIN))\n",
        }

        result = validate_seed(seed)

        self.assertFalse(result.valid)
        self.assertTrue(any("forbidden token" in error and "bsource" in error for error in result.errors))
        self.assertTrue(any("forbidden token" in error and "laplace" in error for error in result.errors))

    def test_rejects_undeclared_placeholders_and_out_of_range_initial_values(self):
        seed = {
            **VALID_SEED,
            "netlist_template": VALID_SEED["netlist_template"] + "R2 VIN VREF resistor r={missing_param}\n",
            "initial_params": {**VALID_SEED["initial_params"], "q_mult": 99},
        }

        result = validate_seed(seed)

        self.assertFalse(result.valid)
        self.assertIn("placeholder 'missing_param' is not declared in param_ranges", result.errors)
        self.assertIn("initial param 'q_mult'=99 is outside [1, 8]", result.errors)

    def test_rejects_missing_active_device_rows_in_devices_csv(self):
        csv_text = (
            "name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa\n"
            "R1,resistor,1,,,,100,5.73u,0.35u,,,true\n"
        )

        result = validate_seed(VALID_SEED, devices_csv_text=csv_text)

        self.assertFalse(result.valid)
        self.assertIn("active device 'Q1' is missing from devices.csv", result.errors)


if __name__ == "__main__":
    unittest.main()
