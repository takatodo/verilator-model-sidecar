module tiny #(
  parameter int unsigned Width = 8
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic [Width-1:0] data_i,
  output logic [Width-1:0] data_o
);
  logic [Width-1:0] state_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= '0;
    end else begin
      state_q <= data_i;
    end
  end

  assign data_o = state_q;
endmodule
