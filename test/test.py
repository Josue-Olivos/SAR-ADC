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
# ui_in[2:1]  = design selector
#               00 = clean
#               01 = manual Trojan
#               10 = automatic Trojan
#               11 = clean/default
#
# ui_in[3]    = manual Trojan enable
#
# uo_out[3:0] = selected live DAC output
# uo_out[4]   = selected sample switch
# uo_out[7:5] = selected FSM state
#
# uio_out[3:0] = stable final ADC conversion code
# uio_oe[3:0]  = 1111, configuring these pins as outputs


DAC_MASK = 0x0F
RESULT_MASK = 0x0F

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
    """Return the live capacitor-DAC control code from uo_out[3:0]."""

    return int(dut.uo_out.value) & DAC_MASK


def get_sample_switch(dut):
    """Return the sample-switch control from uo_out[4]."""

    return (int(dut.uo_out.value) >> 4) & 0x01


def get_state(dut):
    """Return the selected controller state from uo_out[7:5]."""

    return (int(dut.uo_out.value) >> 5) & 0x07


def get_result_code(dut):
    """Return the stable completed ADC code from uio_out[3:0]."""

    return int(dut.uio_out.value) & RESULT_MASK


def get_result_output_enable(dut):
    """Return the output-enable settings for uio[3:0]."""

    return int(dut.uio_oe.value) & RESULT_MASK


# ================================================================
# INPUT HELPERS
# ================================================================

async def set_design(
    dut,
    design_select,
    trojan_enable=False,
):
    """
    Set the design selector and manual-Trojan enable.

    ui_in[2:1] = design selector
    ui_in[3]   = manual-Trojan enable
    ui_in[0]   = comparator output and is preserved
    """

    await NextTimeStep()

    current_value = int(dut.ui_in.value)

    # Preserve comparator bit ui_in[0] and unused upper bits.
    value = current_value & ~0x0E

    value |= (design_select & 0x03) << 1

    if trojan_enable:
        value |= 0x08

    dut.ui_in.value = value


async def set_comparator(dut, comparator_value):
    """
    Drive ui_in[0] without changing the selector or Trojan-enable bits.
    """

    await NextTimeStep()

    current_value = int(dut.ui_in.value)
    dut.ui_in.value = (
        (current_value & 0xFE)
        | (comparator_value & 0x01)
    )


async def reset_dut(dut):
    """Apply the active-low Tiny Tapeout reset."""

    dut.ena.value = 1
    dut.uio_in.value = 0

    # Clear only the comparator input. Preserve ui_in[3:1].
    await set_comparator(dut, 0)

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

    The model responds whenever uo_out changes. Only ui_in[0] is
    modified, so the design selector and Trojan-enable inputs remain
    unchanged.
    """

    await set_comparator(
        dut,
        1 if input_code >= get_dac_code(dut) else 0,
    )

    while True:
        await ValueChange(dut.uo_out)
        await ReadOnly()

        trial_code = get_dac_code(dut)

        comparator_value = (
            1 if input_code >= trial_code else 0
        )

        await set_comparator(
            dut,
            comparator_value,
        )


# ================================================================
# STATE HELPERS
# ================================================================

async def wait_for_output_change(dut):
    """Wait for a visible uo_out change with a timeout."""

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
        return

    for _ in range(max_changes):
        await wait_for_output_change(dut)

        if get_state(dut) == target_state:
            return

    raise AssertionError(
        f"Timed out waiting for state {target_state}. "
        f"Current state={get_state(dut)}, "
        f"DAC={get_dac_code(dut):04b}, "
        f"result={get_result_code(dut):04b}"
    )


async def wait_for_latched_result(dut):
    """
    Wait for one conversion and return the stable result register.

    The controller first enters DONE. On the following adc_tick, the
    DONE state copies the completed SAR code into result_code and
    returns to SAMPLE. Therefore, this helper waits until DONE is
    reached and then waits until the controller leaves DONE before
    reading uio_out[3:0].
    """

    if get_state(dut) == DONE:
        while get_state(dut) == DONE:
            await wait_for_output_change(dut)

    await wait_for_state(
        dut,
        DONE,
    )

    while get_state(dut) == DONE:
        await wait_for_output_change(dut)

    # wait_for_output_change() already leaves us in ReadOnly,
    # so read the stable result directly.
    return get_result_code(dut)


async def run_conversion(
    dut,
    input_code,
    design_name,
):
    """Run one SAR conversion and verify the stable final code."""

    comparator_task = cocotb.start_soon(
        comparator_model(
            dut,
            input_code,
        )
    )

    result = await wait_for_latched_result(dut)

    comparator_task.cancel()

    dut._log.info(
        f"{design_name}: "
        f"input={input_code:04b} ({input_code}), "
        f"latched result={result:04b} ({result})"
    )

    assert result == input_code, (
        f"{design_name} failed. "
        f"Expected stable result {input_code:04b}, "
        f"received {result:04b}"
    )

    return result


# ================================================================
# QUICK COMBINED-DESIGN TEST
# ================================================================

@cocotb.test()
async def test_three_designs(dut):
    """
    Verify all three integrated SAR controllers with one conversion
    per selector setting.

    Long manual and automatic Trojan trigger windows are intentionally
    not simulated in this quick integration test.
    """

    dut._log.info(
        "Start quick three-design result-register test"
    )

    clock = Clock(
        dut.clk,
        20,
        unit="ns",
    )

    cocotb.start_soon(clock.start())

    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 2)

    assert get_result_output_enable(dut) == 0x0F, (
        "uio[3:0] must be configured as outputs. "
        f"Observed uio_oe[3:0]={get_result_output_enable(dut):04b}"
    )

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
    assert get_result_code(dut) == 0

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
    assert get_result_code(dut) == 0

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
    assert get_result_code(dut) == 0

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
    assert get_result_code(dut) == 0

    await run_conversion(
        dut,
        input_code,
        "Default selector",
    )

    dut._log.info(
        "All controller selections and stable result outputs passed"
    )
