# -*- coding: utf-8 -*-
"""Classes and methods for running xspectra.x with AiiDA."""
import os
from aiida import orm
from aiida.common import exceptions
from aiida.orm import Dict, FolderData, RemoteData, SinglefileData, XyData
from aiida.common.datastructures import CalcInfo, CodeInfo
from aiida.plugins import DataFactory
from aiida_quantumespresso.calculations import _lowercase_dict, _uppercase_dict, _pop_parser_options
from aiida_quantumespresso.utils.convert import convert_input_to_namelist_entry
from aiida_quantumespresso.calculations.namelists import NamelistsCalculation

KpointsData = DataFactory('array.kpoints')

class XspectraCalculation(NamelistsCalculation): 
    """CalcJob implementation for the xspectra.x code of Quantum ESPRESSO."""

    _Plotcore_FILENAME = 'plotcore.out'
    _Spectrum_FILENAME = 'xanes.dat'
    _default_namelists = ['INPUT_XSPECTRA', 'PLOT', 'PSEUDOS', 'CUT_OCC']
    _blocked_keywords = [
        ('INPUT_XSPECTRA', 'outdir', NamelistsCalculation._OUTPUT_SUBFOLDER),
        ('INPUT_XSPECTRA', 'prefix', NamelistsCalculation._PREFIX),
        ('PSEUDOS', 'filecore', _Plotcore_FILENAME)
    ]
    _internal_retrieve_list = [_Spectrum_FILENAME]
    _retrieve_singlefile_list = []
    _retrieve_temporary_list = []
    _default_parser = 'quantumespresso.xspectra'

    @classmethod
    def define(cls, spec):
        """Define the process specification."""

        super().define(spec)

        spec.input('parent_folder', valid_type=(orm.RemoteData, orm.FolderData), required=True)
        spec.input('core_wfc_data', valid_type=SinglefileData,
                   required=True,
                   help='Core wavefunction data, generated by the upf2plotcore.sh utility')
        spec.input('bz_sampling', valid_type=KpointsData,
                   required=True,
                   help='The K-point sampling to be used for the XSpectra calculation')
        spec.output('output_parameters', valid_type=Dict)
        spec.output('spectra', valid_type=XyData)
        spec.default_output_node = 'output_parameters'

        spec.exit_code(310, 'ERROR_OUTPUT_STDOUT_READ',
            message='The stdout output file could not be read.')
        spec.exit_code(312, 'ERROR_OUTPUT_STDOUT_INCOMPLETE',
            message='The stdout output file was incomplete probably because the calculation got interrupted.')
        spec.exit_code(313, 'ERROR_OUTPUT_ABSORBING_SPECIES_WRONG',
            message='The absorbing atom species was set incorrectly, check and ensure that the index value of '
                       '"xiabs" correctly refers to the ATOMIC SPECIES containing the core-hole (where the index '
                       'starts from 1).')
        spec.exit_code(314, 'ERROR_OUTPUT_ABSORBING_SPECIES_ZERO',
            message='The absorbing atom species was set to 0 or less.')
        spec.exit_code(330, 'ERROR_READING_XSPECTRA_FILE',
            message='The xspectra output file could not be read from the retrieved folder.')

    def _get_following_text(self):
        """Return any optional text that is to be written after the normal namelists in the input file.

        By default, no text follows the namelists section. If in a sub class, any additional information needs to be
        added to the input file, this method can be overridden to return the lines that should be appended.
        """
        # pylint: disable=no-self-use
        kpmesh = self.inputs.bz_sampling.get_kpoints_mesh()
        full_list = kpmesh[0] + kpmesh[1]
        list_string = str(full_list)
        kpoint_string = list_string.replace('[', '').replace(']', '').replace(',', '')

        return kpoint_string

    def prepare_for_submission(self, folder):
        """Prepare the calculation job for submission by transforming input nodes into input files.

        In addition to the input files being written to the sandbox folder, a `CalcInfo` instance will be returned that
        contains lists of files that need to be copied to the remote machine before job submission, as well as file
        lists that are to be retrieved after job completion.
        :param folder: a sandbox folder to temporarily write files on disk.
        :return: :py:`~aiida.common.datastructures.CalcInfo` instance.
        """
        # pylint: disable=too-many-branches,too-many-statements
        if 'settings' in self.inputs:
            settings = _uppercase_dict(self.inputs.settings.get_dict(), dict_name='settings')
        else:
            settings = {}

        following_text = self._get_following_text()

        if 'parameters' in self.inputs:
            parameters = _uppercase_dict(self.inputs.parameters.get_dict(), dict_name='parameters')
            parameters = {k: _lowercase_dict(v, dict_name=k) for k, v in parameters.items()}
        else:
            parameters = {}

        # =================== NAMELISTS AND CARDS ========================
        try:
            namelists_toprint = settings.pop('NAMELISTS')
            if not isinstance(namelists_toprint, list):
                raise exceptions.InputValidationError(
                    "The 'NAMELISTS' value, if specified in the settings input node, must be a list of strings"
                )
        except KeyError:  # list of namelists not specified; do automatic detection
            namelists_toprint = self._default_namelists

        parameters = self.set_blocked_keywords(parameters)
        parameters = self.filter_namelists(parameters, namelists_toprint)
        file_content = self.generate_input_file(parameters)
        file_content += '\n' + following_text
        input_filename = self.inputs.metadata.options.input_filename
        with folder.open(input_filename, 'w') as infile:
            infile.write(file_content)

        symlink = settings.pop('PARENT_FOLDER_SYMLINK', False)

        remote_copy_list = []
        local_copy_list = []
        remote_symlink_list = []

        ptr = remote_symlink_list if symlink else remote_copy_list

        # copy remote output dir, if specified
        parent_calc_folder = self.inputs.get('parent_folder', None)
        if parent_calc_folder is not None:
            if isinstance(parent_calc_folder, RemoteData):
                parent_calc_out_subfolder = settings.pop('PARENT_CALC_OUT_SUBFOLDER', self._INPUT_SUBFOLDER)
                ptr.append((
                    parent_calc_folder.computer.uuid,
                    os.path.join(parent_calc_folder.get_remote_path(),
                                 parent_calc_out_subfolder), self._OUTPUT_SUBFOLDER
                ))
            elif isinstance(parent_calc_folder, FolderData): 
                for filename in parent_calc_folder.list_object_names():
                    local_copy_list.append(
                        (parent_calc_folder.uuid, filename, os.path.join(self._OUTPUT_SUBFOLDER, filename))
                    )
            elif isinstance(parent_calc_folder, SinglefileData):
                single_file = parent_calc_folder
                local_copy_list.append((single_file.uuid, single_file.filename, single_file.filename))

        # append the core wavefunction data node to the copy list
        core_file = self.inputs.core_wfc_data
        core_file_info = (core_file.uuid, core_file.filename, core_file.filename)
        local_copy_list.append(core_file_info)           

        codeinfo = CodeInfo()
        codeinfo.cmdline_params = settings.pop('CMDLINE', [])
        codeinfo.stdin_name = self.inputs.metadata.options.input_filename
        codeinfo.stdout_name = self.inputs.metadata.options.output_filename
        codeinfo.code_uuid = self.inputs.code.uuid

        calcinfo = CalcInfo()
        calcinfo.uuid = str(self.uuid)
        calcinfo.codes_info = [codeinfo]
        calcinfo.local_copy_list = local_copy_list
        calcinfo.remote_copy_list = remote_copy_list
        calcinfo.remote_symlink_list = remote_symlink_list

        # Retrieve by default the output file and the xml file
        calcinfo.retrieve_list = []
        calcinfo.retrieve_list.append(self.inputs.metadata.options.output_filename)
        calcinfo.retrieve_list += settings.pop('ADDITIONAL_RETRIEVE_LIST', [])
        calcinfo.retrieve_list += self._internal_retrieve_list

        calcinfo.retrieve_temporary_list = self._retrieve_temporary_list
        calcinfo.retrieve_singlefile_list = self._retrieve_singlefile_list

        # We might still have parser options in the settings dictionary: pop them.
        _pop_parser_options(self, settings)

        if settings:
            unknown_keys = ', '.join(list(settings.keys()))
            raise exceptions.InputValidationError(f'`settings` contained unexpected keys: {unknown_keys}')

        return calcinfo
