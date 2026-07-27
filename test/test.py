# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import (
    ClockCycles,
    NextTimeStep,
    ReadOnly,
    ValueChange,
    with_timeout,
)


# ================================================================
# TINY TAPEOUT PIN MAPPING
# ================================================================
#
# ui_in[0]    = comparator output
#
# uio_in[1:0] = design selector
#               00 = clean
#               01 = manual Trojan
#               10 = automatic Trojan
#               11 = clean/default
#
# uio_in[2]   = manual Trojan enable
#
# uo_out[3:0] = selected DAC output
# uo_out[4]   = selected sample switch
# uo_out[7:5] = selected FSM state


DAC_MASK = 0x0F

SAMPLE = 0
HOLD = 1
SET_BIT = 2
WAIT_DAC = 3
READ_COMP = 4
DONE = 5

CLEAN_SELECT = 0b00
MANUAL_SELECT = 0b01
AUTO_SELECT = 0b10
DEFAULT_SELECT = 0b11

OUTPUT_TIMEOUT_US = 2_000


# ================================================================
# OUTPUT HELPERS
# ================================================================

def get_dac_code(dut):
    """Return uo_out[3:0]."""

    return int(dut.uo_out.value) & DAC_MASK


def get_sample_switch(dut):
    """Return uo_out[4]."""

    return (int(dut.uo_out.value) >> 4) & 0x01


def get_state(dut):
    """Return uo_out[7:5]."""

    return (int(dut.uo_out.value) >> 5) & 0x07


# ================================================================
# INPUT HELPERS
# ================================================================

async def set_design(
    dut,
    design_select,
    trojan_enable=False,
):
    """
    Set the design selector and manual Trojan enable.

    uio_in[1:0] = design selector
    uio_in[2]   = manual Trojan enable
    """

    value = design_select & 0x03

    if trojan_enable:
        value |= 0x04

    await NextTimeStep()
    dut.uio_in.value = value


async def reset_dut(dut):
    """Apply the active-low Tiny Tapeout reset."""

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)

    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 10)


async def select_and_reset(
    dut,
    design_select,
    trojan_enable=False,
):
    """
    Select one design, then reset all controllers.

    Resetting after changing the selector guarantees that the newly
    selected controller begins in SAMPLE.
    """

    await set_design(
        dut,
        design_select,
        trojan_enable,
    )

    await reset_dut(dut)


# ================================================================
# COMPARATOR MODEL
# ================================================================

async def comparator_model(dut, input_code):
    """
    Model the external comparator.

    Comparator HIGH means:

        input_code >= selected physical DAC code

    The model waits for uo_out changes instead of checking every
    high-frequency clock edge.
    """

    await NextTimeStep()

    dut.ui_in.value = (
        1 if input_code >= get_dac_code(dut) else 0
    )

    while True:
        await ValueChange(dut.uo_out)
        await ReadOnly()

        trial_code = get_dac_code(dut)

        comparator_value = (
            1 if input_code >= trial_code else 0
        )

        await NextTimeStep()
        dut.ui_in.value = comparator_value


# ================================================================
# STATE HELPERS
# ================================================================

async def wait_for_output_change(dut):
    """Wait for a visible output change with a timeout."""

    await with_timeout(
        ValueChange(dut.uo_out),
        OUTPUT_TIMEOUT_US,
        "us",
    )

    await ReadOnly()


async def wait_for_state(
    dut,
    target_state,
    max_changes=40,
):
    """Wait until the selected controller reaches target_state."""

    if get_state(dut) == target_state:
        return get_dac_code(dut)

    for _ in range(max_changes):
        await wait_for_output_change(dut)

        if get_state(dut) == target_state:
            return get_dac_code(dut)

    raise AssertionError(
        f"Timed out waiting for state {target_state}. "
        f"Current state={get_state(dut)}, "
        f"DAC={get_dac_code(dut):04b}"
    )


async def wait_for_conversion_done(dut):
    """Wait for the next completed conversion."""

    if get_state(dut) == DONE:
        while get_state(dut) == DONE:
            await wait_for_output_change(dut)

    return await wait_for_state(
        dut,
        DONE,
    )


async def run_conversion(
    dut,
    input_code,
    design_name,
):
    """Run one SAR conversion and verify its result."""

    comparator_task = cocotb.start_soon(
        comparator_model(
            dut,
            input_code,
        )
    )

    result = await wait_for_conversion_done(dut)

    comparator_task.cancel()

    dut._log.info(
        f"{design_name}: "
        f"input={input_code:04b} ({input_code}), "
        f"result={result:04b} ({result})"
    )

    assert result == input_code, (
        f"{design_name} failed. "
        f"Expected {input_code:04b}, "
        f"received {result:04b}"
    )

    return result


# ================================================================
# QUICK COMBINED-DESIGN TEST
# ================================================================

@cocotb.test()
async def test_three_designs(dut):
    """
    Quickly verify all three integrated SAR controllers.

    Only one conversion is run for each selector value. The long
    Trojan trigger windows are intentionally not simulated here.
    """

    dut._log.info(
        "Start quick three-design integration test"
    )

    clock = Clock(
        dut.clk,
        20,
        unit="ns",
    )

    cocotb.start_soon(clock.start())

    input_code = 10

    # ------------------------------------------------------------
    # CLEAN DESIGN: selector 00
    # ------------------------------------------------------------

    await select_and_reset(
        dut,
        CLEAN_SELECT,
    )

    assert get_state(dut) == SAMPLE
    assert get_sample_switch(dut) == 1
    assert get_dac_code(dut) == 0

    await run_conversion(
        dut,
        input_code,
        "Clean design",
    )

    # ------------------------------------------------------------
    # MANUAL TROJAN DESIGN: selector 01
    #
    # Trojan enable remains low, so this must behave cleanly.
    # ------------------------------------------------------------

    await select_and_reset(
        dut,
        MANUAL_SELECT,
        trojan_enable=False,
    )

    assert get_state(dut) == SAMPLE
    assert get_sample_switch(dut) == 1
    assert get_dac_code(dut) == 0

    await run_conversion(
        dut,
        input_code,
        "Manual-Trojan design, disabled",
    )

    # ------------------------------------------------------------
    # AUTOMATIC TROJAN DESIGN: selector 10
    #
    # The first conversion occurs before the automatic trigger.
    # ------------------------------------------------------------

    await select_and_reset(
        dut,
        AUTO_SELECT,
    )

    assert get_state(dut) == SAMPLE
    assert get_sample_switch(dut) == 1
    assert get_dac_code(dut) == 0

    await run_conversion(
        dut,
        input_code,
        "Automatic-Trojan design, pre-trigger",
    )

    # ------------------------------------------------------------
    # RESERVED SELECTOR: selector 11
    #
    # project.v maps this value back to the clean controller.
    # ------------------------------------------------------------

    await select_and_reset(
        dut,
        DEFAULT_SELECT,
    )

    assert get_state(dut) == SAMPLE
    assert get_sample_switch(dut) == 1
    assert get_dac_code(dut) == 0

    await run_conversion(
        dut,
        input_code,
        "Default selector",
    )

    dut._log.info(
        "All three controller selections passed"
    )
