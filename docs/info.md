## How it works

This project implements the digital control logic for a 4-bit successive approximation register analog-to-digital converter, or SAR ADC.

The analog portion of the ADC is built externally using a binary-weighted capacitor array, analog switches, and a comparator. The Tiny Tapeout design controls the sampling switch and the four capacitor DAC switches.

During each conversion, the controller performs the following sequence:

1. The sample switch is enabled so the capacitor array can sample the analog input voltage.
2. The sample switch is disabled to hold the sampled voltage.
3. The controller tests the most significant bit by enabling the corresponding capacitor switch.
4. The external comparator determines whether the trial DAC voltage is above or below the sampled input voltage.
5. Based on the comparator result, the controller either keeps or clears the trial bit.
6. The same process is repeated for the remaining bits, from the most significant bit to the least significant bit.
7. After all four bits have been tested, the `dac` outputs contain the final 4-bit conversion result.

The controller continuously repeats this process. A clock divider generates a slower enable pulse for the SAR state machine so the external capacitor DAC and comparator have time to settle between steps.

### Pin mapping

* `ui_in[0]`: External comparator output
* `uo_out[0]`: DAC bit 0, least significant bit
* `uo_out[1]`: DAC bit 1
* `uo_out[2]`: DAC bit 2
* `uo_out[3]`: DAC bit 3, most significant bit
* `uo_out[4]`: Sample-switch control
* `uo_out[7:5]`: Current state-machine state for debugging

The bidirectional `uio` pins are not used and are configured as inputs.

## How to test

Connect the Tiny Tapeout outputs to the external capacitor DAC and analog switches according to the pin mapping.

Connect the output of the external comparator to `ui_in[0]`. The comparator output must use a voltage level that is compatible with the Tiny Tapeout digital input pins.

Apply a known analog voltage to the ADC input and provide the reference voltage used by the capacitor DAC. The controller will automatically begin performing conversions after reset is released.

Observe `uo_out[3:0]` using LEDs, a logic analyzer, an oscilloscope, or a microcontroller. These four pins contain the current SAR result, with `uo_out[3]` as the most significant bit and `uo_out[0]` as the least significant bit.

For a 4-bit ADC, the expected output code can be estimated using:

[
\text{ADC code} \approx \frac{V_{IN}}{V_{REF}} \times 15
]

For example, with a 3.3 V reference and an input voltage near half of the reference voltage, the expected result should be approximately `0111` or `1000`.

The `sample_sw` output can be observed to verify the sampling and conversion phases. The state outputs on `uo_out[7:5]` can also be monitored to confirm that the state machine advances through the sample, hold, bit-test, comparator-read, and done states.

## External hardware

The project requires an external analog front end because Tiny Tapeout provides digital logic only.

The external hardware includes:

* Binary-weighted capacitor array for the 4-bit charge-redistribution DAC
* Analog switches, such as a CD4066, for controlling the capacitor connections
* External voltage comparator, such as an LM393 or a faster compatible comparator
* Sample-and-hold analog switch
* Comparator pull-up resistor when using an open-collector comparator output
* Reference-voltage source
* Analog input signal source
* Decoupling capacitors for the external integrated circuits
* Breadboard or custom printed circuit board
* Logic analyzer, oscilloscope, LEDs, or microcontroller for observing the digital output
