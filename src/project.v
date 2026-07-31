`default_nettype none

module tt_um_josue_olivos_sar_adc (

    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,

    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,

    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);


    //============================================================
    // TINY TAPEOUT PIN MAPPING
    //============================================================
    //
    // ui_in[0]:
    //     External comparator output
    //
    // ui_in[2:1]:
    //     00 = clean SAR ADC
    //     01 = manually enabled Trojan SAR ADC
    //     10 = automatically triggered Trojan SAR ADC
    //     11 = clean SAR ADC
    //
    // ui_in[3]:
    //     Manual Trojan enable
    //
    // uo_out[3:0]:
    //     Selected physical DAC output
    //
    // uo_out[4]:
    //     Selected sample-switch output
    //
    // uo_out[7:5]:
    //     Selected SAR state
    //
    // uio_out[3:0]:
    //     Stable final 4-bit ADC conversion code
    //
    // uio[7:4]:
    //     Unused inputs


    wire       comp_out;
    wire [1:0] design_select;
    wire       manual_trojan_enable;

    assign comp_out             = ui_in[0];
    assign design_select        = ui_in[2:1];
    assign manual_trojan_enable = ui_in[3];


    //============================================================
    // SHARED CLOCK DIVIDER
    //============================================================

    /*
     * Tiny Tapeout simulation and configuration commonly use a
     * 50 MHz clock:
     *
     *     CLOCK_PERIOD = 20 ns
     *
     * ADC_STEP_HZ determines how often the SAR state machine moves
     * to its next state.
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
    // SHARED COMPARATOR SYNCHRONIZER
    //============================================================

    /*
     * The external comparator output is asynchronous relative to
     * the Tiny Tapeout clock.
     *
     * Two flip-flops reduce the possibility of metastability
     * reaching the SAR controllers.
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
    // DESIGN SELECTION
    //============================================================

    wire clean_selected;
    wire manual_selected;
    wire auto_selected;

    /*
     * Selector 11 defaults to the clean design.
     */

    assign clean_selected =
        (design_select == 2'b00) ||
        (design_select == 2'b11);

    assign manual_selected =
        (design_select == 2'b01);

    assign auto_selected =
        (design_select == 2'b10);


    //============================================================
    // INDIVIDUAL ADC TICKS
    //============================================================

    /*
     * Only the selected SAR controller receives adc_tick.
     *
     * This is important because the comparator responds only to
     * the DAC code that is physically selected at uo_out.
     */

    wire clean_tick;
    wire manual_tick;
    wire auto_tick;

    assign clean_tick =
        adc_tick && clean_selected;

    assign manual_tick =
        adc_tick && manual_selected;

    assign auto_tick =
        adc_tick && auto_selected;


    //============================================================
    // INDIVIDUAL CONTROLLER RESETS
    //============================================================

    /*
     * Unselected designs are held in reset.
     *
     * When a design becomes selected, it starts from the SAMPLE
     * state rather than resuming in the middle of a conversion.
     */

    wire clean_rst_n;
    wire manual_rst_n;
    wire auto_rst_n;

    assign clean_rst_n =
        rst_n && clean_selected;

    assign manual_rst_n =
        rst_n && manual_selected;

    assign auto_rst_n =
        rst_n && auto_selected;


    //============================================================
    // CLEAN SAR ADC INSTANCE
    //============================================================

    wire [3:0] clean_dac;
    wire [3:0] clean_result;
    wire       clean_sample_sw;
    wire [2:0] clean_state;

    sar_adc_clean clean_controller (

        .clk       (clk),
        .rst_n     (clean_rst_n),

        .adc_tick  (clean_tick),
        .comp_sync (comp_sync),

        .sample_sw (clean_sample_sw),
        .dac         (clean_dac),
        .result_code (clean_result),
        .state_out   (clean_state)
    );


    //============================================================
    // MANUAL-TROJAN SAR ADC INSTANCE
    //============================================================

    wire [3:0] manual_dac;
    wire [3:0] manual_result;
    wire       manual_sample_sw;
    wire [2:0] manual_state;

    sar_adc_manual_trojan manual_controller (

        .clk           (clk),
        .rst_n         (manual_rst_n),

        .adc_tick      (manual_tick),
        .comp_sync     (comp_sync),

        .trojan_enable (manual_trojan_enable),

        .sample_sw     (manual_sample_sw),
        .dac           (manual_dac),
        .result_code   (manual_result),
        .state_out     (manual_state)
    );


    //============================================================
    // AUTOMATIC-TROJAN SAR ADC INSTANCE
    //============================================================

    wire [3:0] auto_dac;
    wire [3:0] auto_result;
    wire       auto_sample_sw;
    wire [2:0] auto_state;

    sar_adc_auto_trojan auto_controller (

        .clk       (clk),
        .rst_n     (auto_rst_n),

        .adc_tick  (auto_tick),
        .comp_sync (comp_sync),

        .sample_sw (auto_sample_sw),
        .dac         (auto_dac),
        .result_code (auto_result),
        .state_out   (auto_state)
    );


    //============================================================
    // OUTPUT MULTIPLEXER
    //============================================================

    reg [3:0] selected_dac;
    reg [3:0] selected_result;
    reg       selected_sample_sw;
    reg [2:0] selected_state;

    always @(*) begin

        /*
         * Default values prevent unintended latch inference.
         */

        selected_dac       = clean_dac;
        selected_result    = clean_result;
        selected_sample_sw = clean_sample_sw;
        selected_state     = clean_state;

        case (design_select)

            // Clean SAR ADC
            2'b00: begin
                selected_dac       = clean_dac;
                selected_result    = clean_result;
                selected_sample_sw = clean_sample_sw;
                selected_state     = clean_state;
            end

            // Manually enabled Trojan
            2'b01: begin
                selected_dac       = manual_dac;
                selected_result    = manual_result;
                selected_sample_sw = manual_sample_sw;
                selected_state     = manual_state;
            end

            // Automatically triggered Trojan
            2'b10: begin
                selected_dac       = auto_dac;
                selected_result    = auto_result;
                selected_sample_sw = auto_sample_sw;
                selected_state     = auto_state;
            end

            // Reserved selector defaults to clean
            default: begin
                selected_dac       = clean_dac;
                selected_result    = clean_result;
                selected_sample_sw = clean_sample_sw;
                selected_state     = clean_state;
            end

        endcase
    end


    //============================================================
    // DEDICATED OUTPUT MAPPING
    //============================================================

    assign uo_out[3:0] = selected_dac;
    assign uo_out[4]   = selected_sample_sw;
    assign uo_out[7:5] = selected_state;


    //============================================================
    // BIDIRECTIONAL PIN CONFIGURATION
    //============================================================

    /*
     * uio[3:0] are outputs carrying the stable final ADC code.
     * uio[7:4] remain inputs and are currently unused.
     *
     * Bit order:
     *     uio[3] = result MSB
     *     uio[0] = result LSB
     */

    assign uio_out[3:0] = selected_result;
    assign uio_out[7:4] = 4'b0000;

    assign uio_oe[3:0]  = 4'b1111;
    assign uio_oe[7:4]  = 4'b0000;


    //============================================================
    // UNUSED INPUT HANDLING
    //============================================================

    wire _unused;

    assign _unused = &{ena,ui_in[7:4],uio_in,1'b0};

endmodule

`default_nettype wire
