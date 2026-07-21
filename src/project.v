/*
 * 4-bit SAR ADC Controller for Tiny Tapeout
 *
 * Copyright (c) 2026 Josue Olivos
 * SPDX-License-Identifier: Apache-2.0
 */

`timescale 1ns / 1ps
`default_nettype none

module tt_um_Josue_Olivos_SAR_ADC (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // Bidirectional input path
    output wire [7:0] uio_out,  // Bidirectional output path
    output wire [7:0] uio_oe,   // 0 = input, 1 = output
    input  wire       ena,      // High while project is enabled
    input  wire       clk,      // Tiny Tapeout clock
    input  wire       rst_n     // Active-low reset
);

    //============================================================
    // TINY TAPEOUT PIN MAPPING
    //============================================================

    /*
     * Dedicated inputs:
     *
     * ui_in[0] = comparator output
     * ui_in[7:1] = unused
     *
     * Dedicated outputs:
     *
     * uo_out[0] = DAC bit 0, LSB
     * uo_out[1] = DAC bit 1
     * uo_out[2] = DAC bit 2
     * uo_out[3] = DAC bit 3, MSB
     * uo_out[4] = sample switch control
     * uo_out[7:5] = current FSM state for debugging
     */

    wire comp_out;

    reg        sample_sw;
    reg [3:0]  dac;
    reg [2:0]  state;

    assign comp_out = ui_in[0];

    assign uo_out[3:0] = dac;
    assign uo_out[4]   = sample_sw;
    assign uo_out[7:5] = state;

    /*
     * The bidirectional Tiny Tapeout pins are not currently used.
     * Keep them configured as inputs.
     */

    assign uio_out = 8'b0000_0000;
    assign uio_oe  = 8'b0000_0000;


    //============================================================
    // CLOCK DIVIDER
    //============================================================

    /*
     * These parameters assume that clk is running at 60 MHz.
     *
     * The FSM advances at ADC_STEP_HZ.
     *
     * This is the state-machine step frequency, not the completed
     * ADC conversion rate.
     */

    parameter integer CLK_FREQ_HZ = 60_000_000;
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
            // adc_tick normally remains low.
            adc_tick <= 1'b0;

            if (divider_count >= DIVIDER_MAX) begin
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
     * The comparator is external to the Tiny Tapeout chip and is
     * therefore asynchronous relative to clk.
     *
     * These two flip-flops reduce the risk of metastability before
     * the comparator value is used by the SAR state machine.
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
    // SAR ADC CONTROL LOGIC
    //============================================================

    localparam [2:0]
        SAMPLE    = 3'd0,
        HOLD      = 3'd1,
        SET_BIT   = 3'd2,
        WAIT_DAC  = 3'd3,
        READ_COMP = 3'd4,
        DONE      = 3'd5;

    reg [1:0] bit_index;

    /*
     * Set this parameter according to the polarity of the external
     * comparator:
     *
     * 1:
     *   Comparator high means the trial DAC bit should be kept.
     *
     * 0:
     *   Comparator low means the trial DAC bit should be kept.
     */

    parameter COMP_HIGH_MEANS_KEEP = 1'b1;

    always @(posedge clk) begin
        if (!rst_n) begin
            state     <= SAMPLE;
            sample_sw <= 1'b1;
            dac       <= 4'b0000;
            bit_index <= 2'd3;
        end
        else if (adc_tick) begin
            case (state)

                // Connect the input signal to the capacitor DAC.
                SAMPLE: begin
                    sample_sw <= 1'b1;
                    dac       <= 4'b0000;
                    bit_index <= 2'd3;
                    state     <= HOLD;
                end

                // Disconnect the input and hold the sampled voltage.
                HOLD: begin
                    sample_sw <= 1'b0;
                    bit_index <= 2'd3;
                    state     <= SET_BIT;
                end

                // Set the current trial bit, beginning with the MSB.
                SET_BIT: begin
                    dac[bit_index] <= 1'b1;
                    state          <= WAIT_DAC;
                end

                // Give the external DAC and comparator time to settle.
                WAIT_DAC: begin
                    state <= READ_COMP;
                end

                // Keep or clear the current trial bit.
                READ_COMP: begin
                    if (COMP_HIGH_MEANS_KEEP) begin
                        if (!comp_sync)
                            dac[bit_index] <= 1'b0;
                    end
                    else begin
                        if (comp_sync)
                            dac[bit_index] <= 1'b0;
                    end

                    // After the LSB decision, conversion is complete.
                    if (bit_index == 2'd0) begin
                        state <= DONE;
                    end
                    else begin
                        bit_index <= bit_index - 1'b1;
                        state     <= SET_BIT;
                    end
                end

                // Preserve the completed ADC result for one FSM step.
                DONE: begin
                    sample_sw <= 1'b1;
                    bit_index <= 2'd3;
                    state     <= SAMPLE;
                end

                default: begin
                    state     <= SAMPLE;
                    sample_sw <= 1'b1;
                    dac       <= 4'b0000;
                    bit_index <= 2'd3;
                end

            endcase
        end
    end


    //============================================================
    // UNUSED INPUTS
    //============================================================

    /*
     * Reference unused inputs so that lint and synthesis tools do
     * not report unnecessary unused-signal warnings.
     */

    wire _unused;

    assign _unused = &{ena, ui_in[7:1], uio_in, 1'b0};

endmodule

`default_nettype wire
