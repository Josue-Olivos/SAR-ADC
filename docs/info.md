## How it works

This project implements three selectable versions of the digital control logic for a 4-bit successive approximation register analog-to-digital converter, or SAR ADC.

The three internal designs are:

1. A clean SAR ADC controller
2. A manually enabled hardware-Trojan SAR ADC controller
3. An automatically triggered hardware-Trojan SAR ADC controller

All three controllers share the same clock divider and comparator synchronizer to reduce the total hardware area. Only the selected controller is allowed to advance through its conversion sequence, and a multiplexer connects the selected controller to the physical Tiny Tapeout outputs.

The analog portion of the ADC is built externally using a binary-weighted capacitor array, analog switches, and a comparator. The Tiny Tapeout design controls the sampling switch and the four capacitor-DAC switches.

During a normal conversion, the selected controller performs the following sequence:

1. The sample switch is enabled so the capacitor array can sample the analog input voltage.
2. The sample switch is disabled to hold the sampled voltage.
3. The controller tests the most significant bit by enabling the corresponding capacitor switch.
4. The external comparator determines whether the trial DAC voltage is above or below the sampled input voltage.
5. Based on the comparator result, the controller either keeps or clears the trial bit.
6. The same process is repeated for the remaining bits, from the most significant bit to the least significant bit.
7. After all four bits have been tested, the `dac` outputs contain the final 4-bit conversion result.
8. The controller then returns to the sampling state and begins another conversion.

A shared clock divider generates a slower enable pulse for the selected SAR state machine. This gives the external capacitor DAC, analog switches, and comparator enough time to settle between conversion steps.

### Design selection

The bidirectional input pins `uio_in[1:0]` select which SAR ADC controller is connected to the physical outputs.

* `uio_in[1:0] = 00`: Clean SAR ADC controller
* `uio_in[1:0] = 01`: Manually enabled Trojan SAR ADC controller
* `uio_in[1:0] = 10`: Automatically triggered Trojan SAR ADC controller
* `uio_in[1:0] = 11`: Clean SAR ADC controller by default

The manual-Trojan controller uses `uio_in[2]` as its enable input.

* `uio_in[2] = 0`: Manual Trojan disabled
* `uio_in[2] = 1`: Manual Trojan enabled

The manual enable input is ignored when the clean or automatic design is selected.

The selector should remain unchanged during a conversion. It is recommended to assert reset after changing the selected design so the newly selected controller begins in the sampling state.

### Clean controller

The clean controller performs the normal SAR conversion process without intentionally modifying the DAC outputs.

### Manually enabled Trojan controller

The manually enabled Trojan controller behaves normally while `uio_in[2]` is low.

When `uio_in[2]` is high, the internal Trojan sequence is enabled. The controller alternates between normal and infected operating periods according to its internal conversion counter. During the infected period, the physical capacitor-DAC control outputs are intentionally modified.

This design allows the Trojan behavior to be turned on and off externally for testing and comparison.

### Automatically triggered Trojan controller

The automatic Trojan controller does not require an external enable signal.

It uses an internal conversion counter to activate the Trojan automatically. During normal operation, most conversions use the unmodified SAR DAC output. During the trigger window, a smaller group of conversions uses intentionally modified DAC-control outputs.

In the current implementation:

* 450 conversions operate normally
* 50 conversions operate with the Trojan active
* The sequence then repeats

This creates an intermittent fault that can be compared against the clean and manually enabled designs.

## Pin mapping

### Dedicated inputs

* `ui_in[0]`: External comparator output
* `ui_in[7:1]`: Unused

### Dedicated outputs

* `uo_out[0]`: Selected DAC bit 0, least significant bit
* `uo_out[1]`: Selected DAC bit 1
* `uo_out[2]`: Selected DAC bit 2
* `uo_out[3]`: Selected DAC bit 3, most significant bit
* `uo_out[4]`: Selected sample-switch control
* `uo_out[5]`: Selected state-machine state bit 0
* `uo_out[6]`: Selected state-machine state bit 1
* `uo_out[7]`: Selected state-machine state bit 2

### Bidirectional pins

The bidirectional pins are configured as digital inputs.

* `uio_in[0]`: Design-select bit 0
* `uio_in[1]`: Design-select bit 1
* `uio_in[2]`: Manual Trojan enable
* `uio_in[7:3]`: Unused

## How to test

Connect the selected Tiny Tapeout outputs to the external capacitor DAC and analog switches according to the pin mapping.

Connect the output of the external comparator to `ui_in[0]`. The comparator output must use voltage levels that are compatible with the Tiny Tapeout digital input pins.

Apply a known analog voltage to the ADC input and provide the reference voltage used by the capacitor DAC. After reset is released, the selected controller will automatically begin performing conversions.

### Testing the clean design

Set:

```text
uio_in[1:0] = 00
uio_in[2]   = 0
```

Apply reset and then release it.

Observe `uo_out[3:0]` using LEDs, a logic analyzer, an oscilloscope, or a microcontroller. These four pins contain the selected SAR result, with `uo_out[3]` as the most significant bit and `uo_out[0]` as the least significant bit.

For a 4-bit ADC, the expected output code can be estimated using:

[
\text{ADC code} \approx \frac{V_{IN}}{V_{REF}} \times 15
]

For example, with a 3.3 V reference and an input voltage near half of the reference voltage, the expected result should be approximately `0111` or `1000`.

### Testing the manually enabled Trojan design

Select the manual design with:

```text
uio_in[1:0] = 01
```

First disable the Trojan:

```text
uio_in[2] = 0
```

Reset the design and verify that it behaves like the clean controller.

Next enable the Trojan:

```text
uio_in[2] = 1
```

Reset the design again and observe the DAC outputs over several conversions. The design should initially behave normally and later enter its infected operating phase.

### Testing the automatic Trojan design

Select the automatic design with:

```text
uio_in[1:0] = 10
```

The value of `uio_in[2]` does not matter for this design.

Reset the design and observe the output over time. The controller should perform 450 normal conversions followed by 50 infected conversions, then repeat the sequence automatically.

The automatic design may require a logic analyzer or oscilloscope with a sufficiently long capture window because the Trojan is active only during part of the conversion sequence.

### Observing controller operation

The `sample_sw` output can be monitored to verify the sampling and conversion phases.

The state outputs on `uo_out[7:5]` can also be monitored to confirm that the selected state machine advances through the following states:

* `000`: Sample
* `001`: Hold
* `010`: Set trial bit
* `011`: Wait for DAC settling
* `100`: Read comparator
* `101`: Conversion complete

When changing between the three controller designs, keep the design-selection pins stable and reset the project before beginning another measurement.

## External hardware

The project requires an external analog front end because Tiny Tapeout provides the digital control logic only.

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
* Logic analyzer, oscilloscope, LEDs, or microcontroller for observing the digital outputs
* Jumper wires or switches for controlling `uio_in[2:0]`

The same external analog hardware can be used to test all three digital controllers. Only the design-selection inputs need to be changed.
