#--------------------------------------------------------------------------------------

#OPTION COMMAND FILE created by Cadence Extraction Quantus UI Version 21.13-s043

#--------------------------------------------------------------------------------------

capacitance \
              -decoupling_factor 1.0 \
              -ground_net "vss"
extract \
              -selection "all" \
              -type "rc_coupled"
extraction_setup \
              -array_vias_spacing auto \
              -max_fracture_length infinite \
              -max_fracture_length_unit "MICRONS" \
              -max_via_array_size "auto" \
              -parasitic_blocking_device_cells_file "/pdk/hn001/QCI_deck/preserveCellList.txt" \
              -net_name_space "SCHEMATIC"
filter_cap \
              -exclude_self_cap true \
              -exclude_floating_nets true \
              -exclude_floating_nets_limit 8000
filter_coupling_cap \
              -coupling_cap_threshold_absolute 0.02 \
              -coupling_cap_threshold_relative 0.005
filter_res \
              -merge_parallel_res true \
              -min_res 0.005 \
              -remove_dangling_res true
rf_analysis \
              -enable_inductance true \
              -substrate_coupling true \
              -frequency_sweep "1e9 40e9 200"
input_db -type calibre \
              -design_cell_name "pll_dco layout EXAMPLE_LIB" \
              -device_property_value 7 \
              -run_name "Design" \
              -directory_name "/proj/wb/verify/QCI_PATH_pll_dco/query_output" \
              -format "DFII" \
              -instance_property_value 6 \
              -layer_map_file "/proj/wb/verify/QCI_PATH_pll_dco/query_output/Design.gds.map" \
              -net_property_value 5 \
              -device_properties_file "/proj/wb/verify/QCI_PATH_pll_dco/query_output/Design.props"
output_db -type extracted_view \
              -cap_component "pcapacitor" \
              -cap_property_name "c" \
              -enable_cellview_check false \
              -device_finger_delimiter "@" \
              -cdl_out_map_directory \
              "/proj/wb/verify/QCI_PATH_pll_dco/" \
              -include_cap_model "false" \
              -include_parasitic_cap_model "false" \
              -include_res_model "false" \
              -include_parasitic_res_model "comment" \
              -res_component "presistor" \
              -res_property_name "r" \
              -view_name "av_ext"
output_setup \
              -directory_name "/proj/wb/verify/QCI_PATH_pll_dco/query_output" \
              -temporary_directory_name "Design"
process_technology \
              -technology_corner \
              "RCWORST" \
              -technology_library_file "/pdk/hn001/setup/assura_tech.lib" \
              -technology_name "HN001" \
              -temperature \
              125
