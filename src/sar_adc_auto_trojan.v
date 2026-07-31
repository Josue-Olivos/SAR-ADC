`default_nettype none

module sar_adc_auto_trojan (

    input  wire       clk,
    input  wire       rst_n,

    // Shared signals from the Tiny Tapeout top-level module
    input  wire       adc_tick,
    input  wire       comp_sync,

    output reg        sample_sw,
    output wire [3:0] dac,
    output reg  [3:0] result_code,
    output wire [2:0] state_out
);

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

    assign state_out = state;


    //============================================================
    // INTERNAL SAR DAC REGISTER
    //============================================================

    reg [3:0] sar_dac;


    //============================================================
    // AUTOMATIC TROJAN
    //============================================================

    /*
     * Normal operation:
     *     counter values 0 through 449
     *
     * Infected operation:
     *     counter values 450 through 499
     *
     * Therefore, 50 out of every 500 conversions are corrupted.
     */

    reg [8:0] trojan_conversion_count;

    wire trojan_active;

    assign trojan_active =
        (trojan_conversion_count >= 9'd450);

    assign dac =
        trojan_active
            ? ~sar_dac
            : sar_dac;


    //============================================================
    // SAR ADC CONTROL LOGIC
    //============================================================

    always @(posedge clk) begin
        if (!rst_n) begin
            state                   <= SAMPLE;
            sample_sw               <= 1'b1;
            sar_dac                 <= 4'b0000;
            result_code             <= 4'b0000;
            bit_index               <= 2'd3;
            trojan_conversion_count <= 9'd0;
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
                // WAIT FOR DAC AND COMPARATOR TO SETTLE
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
                    // Latch the internal SAR decision code. The physical
                    // DAC output may be inverted by the Trojan.
                    result_code <= sar_dac;

                    sample_sw <= 1'b1;
                    bit_index <= 2'd3;
                    state     <= SAMPLE;

                    /*
                     * Count completed conversions.
                     *
                     * After count 499, wrap back to zero.
                     */

                    if (
                        trojan_conversion_count == 9'd499
                    ) begin
                        trojan_conversion_count <= 9'd0;
                    end
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
                    result_code             <= 4'b0000;
                    bit_index               <= 2'd3;
                    trojan_conversion_count <= 9'd0;
                end

            endcase
        end
    end

endmodule

`default_nettype wire
