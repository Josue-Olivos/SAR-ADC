python
# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import (
    ClockCycles,
    NextTimeStep,
    ReadOnly,
    RisingEdge,
)


# ================================================================
# TINY TAPEOUT PIN MAPPING
# ================================================================
#
# ui_in[0]    = external comparator output
# uio_in[0]   = experimental Trojan enable
#
# uo_out[3:0] = physical DAC switch outputs
# uo_out[4]   = sample-switch control
# uo_out[7:5] = SAR state-machine state


DAC_MASK = 0x0F

SAMPLE = 0
HOLD = 1
SET_BIT = 2
WAIT_DAC = 3
READ_COMP = 4
DONE = 5


# ================================================================
# OUTPUT HELPERS
# ================================================================

def get_dac_code(dut):
    """Return the physical 4-bit DAC output from uo_out[3:0]."""

    return int(dut.uo_out.value) & DAC_MASK


def get_sample_switch(dut):
    """Return the sample-switch output from uo_out[4]."""

    return (int(dut.uo_out.value) >> 4) & 0x01


def get_state(dut):
    """Return the SAR state from uo_out[7:5]."""

    return (int(dut.uo_out.value) >> 5) & 0x07


# ================================================================
# INPUT HELPERS
# ================================================================

async def set_trojan_enable(dut, enabled):
    """
    Set uio_in[0], the experimental Trojan-enable input.

    The NextTimeStep trigger ensures the simulator has left the
    ReadOnly phase before the testbench drives uio_in.
    """

    await NextTimeStep()

    if enabled:
        dut.uio_in.value = 0x01
    else:
        dut.uio_in.value = 0x00


async def assert_reset(dut):
    """Assert the active-low Tiny Tapeout reset."""

    await NextTimeStep()

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)


async def release_reset(dut):
    """Release the active-low Tiny Tapeout reset."""

    await NextTimeStep()

    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 10)


async def reset_dut(dut):
    """Apply and release reset."""

    dut._log.info("Reset")

    await assert_reset(dut)
    await release_reset(dut)


# ================================================================
# EXTERNAL ANALOG-HARDWARE MODEL
# ================================================================

async def comparator_model(dut, input_code):
    """
    Behavioral model of the external comparator and capacitor DAC.

    input_code represents an ideal analog input value from 0 to 15.

    The model reads the physical DAC control outputs from
    uo_out[3:0]. This means it also observes the inverted outputs
    while the Trojan phase is active.

    Comparator HIGH means:

        input_code >= physical DAC code
    """

    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()

        trial_code = get_dac_code(dut)

        comparator_value = 1 if input_code >= trial_code else 0

        # Leave the ReadOnly phase before driving ui_in.
        await NextTimeStep()

        dut.ui_in.value = comparator_value


# ================================================================
# CONVERSION HELPERS
# ================================================================

async def wait_for_conversion_done(dut, timeout_cycles=20_000):
    """
    Wait for the next complete SAR conversion.

    If the controller is already in DONE, first wait for it to
    leave DONE. Then wait for the following DONE state.
    """

    # Leave the previous DONE state if necessary.
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()

        if get_state(dut) != DONE:
            break
    else:
        raise AssertionError("SAR controller remained stuck in DONE")

    # Wait for the next conversion to finish.
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()

        if get_state(dut) == DONE:
            return get_dac_code(dut)

    raise AssertionError(
        f"Timed out waiting for DONE: "
        f"state={get_state(dut)}, "
        f"DAC={get_dac_code(dut):04b}"
    )


async def run_conversion(dut, input_code, log_result=False):
    """Wait for one conversion and return its physical DAC output."""

    result = await wait_for_conversion_done(dut)

    if log_result:
        dut._log.info(
            f"Input={input_code:04b} ({input_code}), "
            f"output={result:04b} ({result})"
        )

    return result


async def start_test(dut):
    """Start the 50 MHz clock and reset the design."""

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)


# ================================================================
# BASIC CLEAN-DESIGN TEST
# ================================================================

@cocotb.test()
async def test_project(dut):
    """Test reset behavior and one normal conversion."""

    dut._log.info("Start basic 4-bit SAR ADC test")

    await start_test(dut)

    assert get_state(dut) == SAMPLE, (
        f"Expected SAMPLE after reset, "
        f"but state was {get_state(dut)}"
    )

    assert get_sample_switch(dut) == 1, (
        "Sample switch should be enabled after reset"
    )

    assert get_dac_code(dut) == 0, (
        f"DAC should be 0000 after reset, "
        f"but was {get_dac_code(dut):04b}"
    )

    await set_trojan_enable(dut, False)

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    result = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert result == input_code, (
        f"Expected {input_code:04b}, "
        f"but received {result:04b}"
    )

    comparator_task.kill()


# ================================================================
# CLEAN INPUT-CODE SWEEP
# ================================================================

@cocotb.test()
async def test_all_input_codes(dut):
    """Test all possible 4-bit inputs with the Trojan disabled."""

    dut._log.info("Start complete clean 4-bit SAR sweep")

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    for input_code in range(16):
        # Hold the design in reset before starting the model.
        await assert_reset(dut)

        comparator_task = cocotb.start_soon(
            comparator_model(dut, input_code)
        )

        await release_reset(dut)
        await set_trojan_enable(dut, False)

        result = await run_conversion(
            dut,
            input_code,
            log_result=True,
        )

        assert result == input_code, (
            f"Input {input_code:04b}: "
            f"expected {input_code:04b}, "
            f"received {result:04b}"
        )

        comparator_task.kill()

    dut._log.info("All 16 clean input codes passed")


# ================================================================
# TROJAN-DISABLED TEST
# ================================================================

@cocotb.test()
async def test_trojan_disabled_stays_clean(dut):
    """
    Verify that no phase switching occurs while uio_in[0] is low.

    The test runs for more than 500 conversions, which would be
    enough to trigger the inverted phase if the Trojan were enabled.
    """

    dut._log.info("Test operation with Trojan disabled")

    await start_test(dut)
    await set_trojan_enable(dut, False)

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    for conversion_number in range(1, 502):
        result = await run_conversion(dut, input_code)

        assert result == input_code, (
            f"Trojan was disabled, but conversion "
            f"{conversion_number} produced {result:04b}; "
            f"expected {input_code:04b}"
        )

        if conversion_number % 100 == 0:
            dut._log.info(
                f"Verified {conversion_number} clean conversions"
            )

    comparator_task.kill()

    dut._log.info(
        "Trojan-disabled operation remained clean "
        "for more than 500 conversions"
    )


# ================================================================
# TROJAN PHASE-SWITCHING TEST
# ================================================================

@cocotb.test()
async def test_trojan_phase_switching(dut):
    """
    Verify the alternating output phases.

    With Trojan enable high:

        Conversions 1-499:
            normal operation

        Conversion 500:
            trojan_phase changes from normal to inverted at DONE

        Conversions 501-999:
            infected output phase

        Conversion 1000:
            trojan_phase changes back to normal at DONE

        Conversion 1001:
            first complete clean conversion after restoration
    """

    dut._log.info(
        "Start 500-normal / 500-inverted Trojan test"
    )

    await start_test(dut)

    input_code = 10
    normal_code = input_code
    inverted_code = (~input_code) & DAC_MASK

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    await set_trojan_enable(dut, True)

    # ------------------------------------------------------------
    # Conversions 1 through 499 must remain normal.
    # ------------------------------------------------------------

    for conversion_number in range(1, 500):
        result = await run_conversion(dut, input_code)

        assert result == normal_code, (
            f"Conversion {conversion_number} should be normal. "
            f"Expected {normal_code:04b}, "
            f"received {result:04b}"
        )

        if conversion_number % 100 == 0:
            dut._log.info(
                f"Verified normal conversion "
                f"{conversion_number}"
            )

    dut._log.info(
        "Conversions 1 through 499 remained normal"
    )

    # ------------------------------------------------------------
    # At the DONE edge of conversion 500, trojan_phase toggles.
    #
    # Conversion 500 was calculated normally, so the internal SAR
    # result is input_code. The physical output immediately becomes
    # the inverse when trojan_phase changes.
    # ------------------------------------------------------------

    result_500 = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert result_500 == inverted_code, (
        f"Conversion 500 should switch the physical output "
        f"to {inverted_code:04b}, "
        f"but produced {result_500:04b}"
    )

    dut._log.info(
        "Trojan inverted-output phase activated "
        "at conversion 500"
    )

    # ------------------------------------------------------------
    # Conversions 501 through 999 occur during the infected phase.
    #
    # Do not require one exact result. The physical DAC output is
    # part of the comparator feedback loop, so infected conversion
    # results can depend on the resulting closed-loop behavior.
    # ------------------------------------------------------------

    for conversion_number in range(501, 1000):
        await run_conversion(dut, input_code)

        if conversion_number % 100 == 0:
            dut._log.info(
                f"Advanced through infected conversion "
                f"{conversion_number}"
            )

    dut._log.info(
        "Completed the infected 500-conversion phase"
    )

    # ------------------------------------------------------------
    # Conversion 1000 toggles the phase back to normal at DONE.
    #
    # Its internal result was calculated while the infected phase
    # was active, so do not require a particular boundary code.
    # ------------------------------------------------------------

    result_1000 = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    dut._log.info(
        f"Conversion 1000 boundary output was "
        f"{result_1000:04b}"
    )

    # ------------------------------------------------------------
    # Conversion 1001 runs completely after normal operation has
    # been restored.
    # ------------------------------------------------------------

    result_1001 = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert result_1001 == normal_code, (
        f"Expected normal operation to return on conversion 1001. "
        f"Expected {normal_code:04b}, "
        f"received {result_1001:04b}"
    )

    comparator_task.kill()

    dut._log.info(
        "Normal operation successfully returned "
        "after the infected phase"
    )


# ================================================================
# EXTERNAL TROJAN-DISABLE TEST
# ================================================================

@cocotb.test()
async def test_disabling_trojan_resets_sequence(dut):
    """
    Verify that pulling uio_in[0] low disables the Trojan.

    The design is first advanced into the infected phase. The
    Trojan-enable input is then cleared. The next complete
    conversion must use normal DAC outputs.
    """

    dut._log.info("Test external Trojan disable input")

    await start_test(dut)

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    await set_trojan_enable(dut, True)

    # Advance through the normal phase and enter the infected phase.
    for conversion_number in range(1, 501):
        await run_conversion(dut, input_code)

        if conversion_number % 100 == 0:
            dut._log.info(
                f"Advanced to conversion {conversion_number}"
            )

    dut._log.info(
        "Controller has entered the infected phase"
    )

    # This call safely leaves the ReadOnly phase before writing.
    await set_trojan_enable(dut, False)

    restored_result = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert restored_result == input_code, (
        f"Disabling the Trojan did not restore normal operation. "
        f"Expected {input_code:04b}, "
        f"received {restored_result:04b}"
    )

    # Verify that several following conversions remain normal.
    for conversion_number in range(1, 6):
        result = await run_conversion(dut, input_code)

        assert result == input_code, (
            f"Post-disable conversion {conversion_number} "
            f"was not normal. Expected {input_code:04b}, "
            f"received {result:04b}"
        )

    comparator_task.kill()

    dut._log.info(
        "External Trojan disable successfully restored "
        "and maintained clean operation"
    )
