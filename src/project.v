/*
 * 4-bit SAR ADC Controller with Experimental Hardware Trojan
 *
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_josue_olivos_sar_adc (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // Bidirectional input path
    output wire [7:0] uio_out,  // Bidirectional output path
    output wire [7:0] uio_oe,   // 0 = input, 1 = output
    input  wire       ena,      // High while design is enabled
    input  wire       clk,      // Tiny Tapeout clock
    input  wire       rst_n     // Active-low reset
);

    //============================================================
    // TINY TAPEOUT PIN MAPPING
    //============================================================

    /*
     * Inputs:
     *
     * ui_in[0]  = external comparator output
     * uio_in[0] = experimental Trojan enable
     *
     * Outputs:
     *
     * uo_out[0] = DAC bit 0, LSB
     * uo_out[1] = DAC bit 1
     * uo_out[2] = DAC bit 2
     * uo_out[3] = DAC bit 3, MSB
     * uo_out[4] = sample-switch control
     * uo_out[7:5] = current FSM state
     */

    wire comp_out;
    wire trojan_enable;

    assign comp_out      = ui_in[0];
    assign trojan_enable = uio_in[0];


    //============================================================
    // CLOCK DIVIDER
    //============================================================

    /*
     * Tiny Tapeout project configuration:
     *
     * System clock:   50 MHz
     * SAR state rate: 100 kHz
     *
     * The state machine advances once every 500 clock cycles.
     */

    parameter integer CLK_FREQ_HZ = 50_000_000;
    parameter integer ADC_STEP_HZ = 100_000;

    localparam integer DIVIDER_MAX =
        (CLK_FREQ_HZ / ADC_STEP_HZ) - 1;

    reg [31:0] divider_count;
    reg        adc_tick;

    always @(posedge clk) begin
        if (!rst_n) begin
            divider_count <= 32'd0;
            adc_tick      <= 1'b0;
        end
        else begin
            adc_tick <= 1'b0;

            if (divider_count == DIVIDER_MAX) begin
                divider_count <= 32'd0;
                adc_tick      <= 1'b1;
            end
            else begin
                divider_count <= divider_count + 1'b1;
            end
        end
    end


    //============================================================
    // COMPARATOR SYNCHRONIZER
    //============================================================

    /*
     * The external comparator is asynchronous relative to clk.
     * Two flip-flops reduce the chance of metastability.
     */

    reg comp_meta;
    reg comp_sync;

    always @(posedge clk) begin
        if (!rst_n) begin
            comp_meta <= 1'b0;
            comp_sync <= 1'b0;
        end
        else begin
            comp_meta <= comp_out;
            comp_sync <= comp_meta;
        end
    end


    //============================================================
    // SAR ADC STATE DEFINITIONS
    //============================================================

    localparam [2:0]
        SAMPLE    = 3'd0,
        HOLD      = 3'd1,
        SET_BIT   = 3'd2,
        WAIT_DAC  = 3'd3,
        READ_COMP = 3'd4,
        DONE      = 3'd5;

    reg [2:0] state;
    reg [1:0] bit_index;

    parameter COMP_HIGH_MEANS_KEEP = 1'b1;


    //============================================================
    // INTERNAL SAR DAC REGISTER
    //============================================================

    /*
     * sar_dac contains the internal SAR trial code.
     *
     * dac_out is the value that appears on the Tiny Tapeout
     * output pins.
     */

    reg  [3:0] sar_dac;
    wire [3:0] dac_out;


    //============================================================
    // EXPERIMENTAL TROJAN CONTROL
    //============================================================

    /*
     * Counts completed conversions from 0 through 499.
     *
     * Nine bits can represent values from 0 through 511.
     */

    reg [8:0] trojan_conversion_count;

    /*
     * trojan_phase:
     *
     * 0 = normal DAC output
     * 1 = inverted DAC output
     */

    reg trojan_phase;

    /*
     * When uio_in[0] is low:
     *
     *     The Trojan is disabled.
     *     The counter is reset.
     *     DAC outputs are normal.
     *
     * When uio_in[0] is high:
     *
     *     500 conversions use normal outputs.
     *     500 conversions use inverted outputs.
     *     The pattern repeats continuously.
     */

    assign dac_out =
        (trojan_enable && trojan_phase)
            ? ~sar_dac
            : sar_dac;


    //============================================================
    // TINY TAPEOUT OUTPUTS
    //============================================================

    assign uo_out[3:0] = dac_out;
    assign uo_out[4]   = sample_sw;
    assign uo_out[7:5] = state;

    /*
     * All bidirectional pins remain configured as inputs.
     *
     * uio_in[0] is used as the Trojan-enable input.
     */

    assign uio_out = 8'b0000_0000;
    assign uio_oe  = 8'b0000_0000;


    //============================================================
    // SAMPLE-SWITCH REGISTER
    //============================================================

    reg sample_sw;


    //============================================================
    // SAR ADC CONTROL LOGIC
    //============================================================

    always @(posedge clk) begin
        if (!rst_n) begin
            state                   <= SAMPLE;
            sample_sw               <= 1'b1;
            sar_dac                 <= 4'b0000;
            bit_index               <= 2'd3;
            trojan_conversion_count <= 9'd0;
            trojan_phase            <= 1'b0;
        end
        else if (adc_tick) begin
            case (state)

                //================================================
                // SAMPLE INPUT
                //================================================

                SAMPLE: begin
                    sample_sw <= 1'b1;
                    sar_dac   <= 4'b0000;
                    bit_index <= 2'd3;
                    state     <= HOLD;
                end


                //================================================
                // HOLD SAMPLED INPUT
                //================================================

                HOLD: begin
                    sample_sw <= 1'b0;
                    bit_index <= 2'd3;
                    state     <= SET_BIT;
                end


                //================================================
                // SET CURRENT TRIAL BIT
                //================================================

                SET_BIT: begin
                    sar_dac[bit_index] <= 1'b1;
                    state              <= WAIT_DAC;
                end


                //================================================
                // WAIT FOR EXTERNAL DAC AND COMPARATOR
                //================================================

                WAIT_DAC: begin
                    state <= READ_COMP;
                end


                //================================================
                // READ COMPARATOR
                //================================================

                READ_COMP: begin
                    if (COMP_HIGH_MEANS_KEEP) begin
                        if (!comp_sync)
                            sar_dac[bit_index] <= 1'b0;
                    end
                    else begin
                        if (comp_sync)
                            sar_dac[bit_index] <= 1'b0;
                    end

                    if (bit_index == 2'd0) begin
                        state <= DONE;
                    end
                    else begin
                        bit_index <= bit_index - 1'b1;
                        state     <= SET_BIT;
                    end
                end


                //================================================
                // CONVERSION COMPLETE
                //================================================

                DONE: begin
                    sample_sw <= 1'b1;
                    bit_index <= 2'd3;
                    state     <= SAMPLE;

                    /*
                     * Disabling the Trojan returns the controller
                     * immediately to the normal phase and resets
                     * the conversion counter.
                     */

                    if (!trojan_enable) begin
                        trojan_conversion_count <= 9'd0;
                        trojan_phase            <= 1'b0;
                    end

                    /*
                     * Toggle the Trojan phase after every 500
                     * completed conversions.
                     */

                    else if (trojan_conversion_count == 9'd499) begin
                        trojan_conversion_count <= 9'd0;
                        trojan_phase            <= ~trojan_phase;
                    end

                    /*
                     * Count the completed conversion.
                     */

                    else begin
                        trojan_conversion_count <=
                            trojan_conversion_count + 1'b1;
                    end
                end


                //================================================
                // RECOVERY
                //================================================

                default: begin
                    state                   <= SAMPLE;
                    sample_sw               <= 1'b1;
                    sar_dac                 <= 4'b0000;
                    bit_index               <= 2'd3;
                    trojan_conversion_count <= 9'd0;
                    trojan_phase            <= 1'b0;
                end

            endcase
        end
    end


    //============================================================
    // UNUSED INPUTS
    //============================================================

    wire _unused;

    assign _unused = &{
        ena,
        ui_in[7:1],
        uio_in[7:1],
        1'b0
    };

endmodule

`default_nettype wire
