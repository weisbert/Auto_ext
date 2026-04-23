#--------------------------------------------------------------------------------------

#OPTION COMMAND FILE created by Cadence Extraction Quantus UI Version 18.21-s340

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
              -parasitic_blocking_device_cells_file "$env(VERIFY_ROOT)/runset/Calibre_QRC/QRC/Ver_Minus_2.1c/CFBETA/QCI_deck/preserveCellList.txt" \
              -net_name_space "SCHEMATIC"
filter_cap \
              -exclude_self_cap true \
              -exclude_floating_nets true \
              -exclude_floating_nets_limit 5000
filter_coupling_cap \
              -coupling_cap_threshold_absolute 0.01 \
              -coupling_cap_threshold_relative 0.001
filter_res \
              -merge_parallel_res true \
              -min_res 0.001 \
              -remove_dangling_res true
input_db -type calibre \
              -design_cell_name "AMP2 layout AMP_LIB" \
              -device_property_value 7 \
              -run_name "Design" \
              -directory_name "/work/cds/verify/QCI_PATH_AMP2/query_output" \
              -format "DFII" \
              -instance_property_value 6 \
              -layer_map_file "/work/cds/verify/QCI_PATH_AMP2/query_output/Design.gds.map" \
              -net_property_value 5 \
              -device_properties_file "/tmpdata/RFIC/rfic_share/bob/cds/verify/QCI_PATH/query_output/Design.props"
output_db -type extracted_view \
              -cap_component "pcapacitor" \
              -cap_property_name "c" \
              -enable_cellview_check false \
              -device_finger_delimiter "@" \
              -cdl_out_map_directory \
              "/tmpdata/RFIC/rfic_share/bob/cds/verify/QCI_PATH/" \
              -include_cap_model "false" \
              -include_parasitic_cap_model "false" \
              -include_res_model "false" \
              -include_parasitic_res_model "comment" \
              -res_component "presistor" \
              -res_property_name "r" \
              -view_name "av_ext"
output_setup \
              -directory_name "/tmpdata/RFIC/rfic_share/bob/cds/verify/QCI_PATH/query_output" \
              -temporary_directory_name "Design"
process_technology \
              -technology_corner \
              "TYPICAL" \
              -technology_library_file "$env(SETUP_ROOT)/assura_tech.lib" \
              -technology_name "HN042" \
              -temperature \
              55.0
