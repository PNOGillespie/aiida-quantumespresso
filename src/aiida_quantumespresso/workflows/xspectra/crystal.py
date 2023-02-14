# -*- coding: utf-8 -*-
"""Workchain to compute all X-ray absorption spectra for a given structure.

Uses QuantumESPRESSO pw.x and xspectra.x.
"""
from aiida import orm
from aiida.common import AttributeDict, ValidationError
from aiida.engine import ToContext, WorkChain, append_, if_
from aiida.orm import UpfData as aiida_core_upf
from aiida.plugins import CalculationFactory, DataFactory, WorkflowFactory
from aiida_pseudo.data.pseudo import UpfData as aiida_pseudo_upf

from aiida_quantumespresso.calculations.functions.xspectra.get_spectra_by_element import get_spectra_by_element
from aiida_quantumespresso.utils.mapping import prepare_process_inputs
from aiida_quantumespresso.workflows.protocols.utils import ProtocolMixin, recursive_merge

PwCalculation = CalculationFactory('quantumespresso.pw')
PwBaseWorkChain = WorkflowFactory('quantumespresso.pw.base')
PwRelaxWorkChain = WorkflowFactory('quantumespresso.pw.relax')
XspectraBaseWorkChain = WorkflowFactory('quantumespresso.xspectra.base')
XspectraCoreWorkChain = WorkflowFactory('quantumespresso.xspectra.core')
XyData = DataFactory('array.xy')


class XspectraCrystalWorkChain(ProtocolMixin, WorkChain):
    """Workchain to compute all X-ray absorption spectra for a given structure using Quantum ESPRESSO.

    The WorkChain follows the process required to compute all the K-edge XAS spectra for each
    element in a given structure. The WorkChain itself firstly calls the PwRelaxWorkChain to
    relax the input structure, then determines the input settings for each XAS
    calculation automatically using ``get_xspectra_structures()``:

        - Firstly the input structure is converted to its conventional standard cell using
          ``spglib`` and detects the space group number for the conventional cell.
        - Symmetry analysis of the standardized structure using ``spglib`` is then used to
          determine the number of non-equivalent atomic sites in the structure for each
          element considered for analysis.

    Using the symmetry data returned from ``get_xspectra_structures``, input structures for
    the XspectraCoreWorkChain are generated from the standardized structure by converting each
    to a supercell with cell dimensions of at least 8.0 angstroms in each periodic dimension -
    required in order to sufficiently reduce the unphysical interaction of the core-hole with
    neighbouring images. The size of the minimum size requirement can be overriden by the
    user if required. The WorkChain then uses the space group number to set the list of
    polarisation vectors for the ``XspectraCoreWorkChain`` to compute for all subsequent
    calculations.
    """

    @classmethod
    def define(cls, spec):
        """Define the process specification."""

        super().define(spec)
        # yapf: disable
        spec.expose_inputs(
            PwRelaxWorkChain,
            namespace='relax',
            exclude=('structure', 'clean_workdir', 'base_final_scf'),
            namespace_options={
                'help': (
                    'Input parameters for the relax process. If not specified at all, the relaxation step is skipped.'
                ),
                'required' : False,
                'populate_defaults' : False,
            }
        )
        spec.expose_inputs(
            XspectraCoreWorkChain,
            namespace='core',
            exclude=(
                'kpoints', 'core_hole_pseudos', 'eps_vectors', 'structure', 'xs_plot'
            ),
            namespace_options={
                'help': ('Input parameters for the basic xspectra workflow (core-hole SCF + XAS.'),
                'validator': None
            }
        )
        spec.input_namespace(
            'core_hole_pseudos',
            # Accept both types of UpfData node
            valid_type=(aiida_core_upf, aiida_pseudo_upf),
            dynamic=True,
            help=(
                'Dynamic namespace for pairs of excited-state pseudopotentials for each absorbing'
                ' element. Must use the mapping "{element}" : {Upf}".'
            )
        )
        spec.input_namespace(
            'gipaw_pseudos',
            # Accept both types of UpfData node
            valid_type=(aiida_core_upf, aiida_pseudo_upf),
            dynamic=True,
            help=(
                'Dynamic namespace for pairs of ground-state pseudopotentials for each absorbing'
                ' element. Must use the mapping "{element}" : {Upf}.'
            )
        )
        spec.input(
            'core_hole_treatments',
            valid_type=orm.Dict,
            required=False,
            help=('Optional dictionary to set core-hole treatment to given elements present. '
                  'The default full-core-hole treatment will be used if not specified.'
                 )
        )
        spec.input(
            'structure',
            valid_type=orm.StructureData,
            help=(
                'Structure to be used for calculation.'
            )
        )
        spec.input(
            'abs_atom_marker',
            valid_type=orm.Str,
            default=lambda: orm.Str('X'),
            help=(
                'The name for the Kind representing the absorbing atom in the structure. '
                'Will be used in all structures generated in ``get_xspectra_structures`` step.'
            ),
        )
        spec.input(
            'upf2plotcore_code',
            valid_type=orm.Code,
            help=(
                'Code node for the upf2plotcore.sh ShellJob code'
            )
        )
        spec.input(
            'elements_list',
            valid_type=orm.List,
            required=False,
            help=(
            'The list of elements to be considered for analysis, each must be valid elements of the periodic table.'
            )
        )
        spec.input(
            'clean_workdir',
            valid_type=orm.Bool,
            default=lambda: orm.Bool(False),
            help=('If `True`, work directories of all called calculations will be cleaned at the end of execution.'),
        )
        spec.input(
            'structure_preparation_settings',
            valid_type=orm.Dict,
            required=False,
            help=(
                'Optional settings dictionary for the ``get_xspectra_structures()`` method.'
            )
        )
        spec.input(
            'spglib_settings',
            valid_type=orm.Dict,
            required=False,
            help=(
                'Optional settings dictionary for the spglib call within ``get_xspectra_structures``.'
            )
        )
        spec.input(
            'return_all_powder_spectra',
            valid_type=orm.Bool,
            default=lambda: orm.Bool(False),
            help=('If ``True``, the WorkChain will return all ``powder_spectrum`` nodes from each '
                  '``XspectraCoreWorkChain`` sub-process.')
        )
        spec.inputs.validator = cls.validate_inputs
        spec.outline(
            cls.setup,
            if_(cls.should_run_relax)(
                cls.run_relax,
                cls.inspect_relax,
            ),
            cls.get_xspectra_structures,
            cls.run_upf2plotcore,
            cls.inspect_upf2plotcore,
            cls.run_all_xspectra_core,
            cls.inspect_all_xspectra_core,
            cls.results,
        )

        spec.exit_code(401, 'ERROR_SUB_PROCESS_FAILED_RELAX', message='The Relax sub process failed')
        spec.exit_code(402, 'ERROR_SUB_PROCESS_FAILED_XSPECTRA', message='One or more XSpectra workflows failed')
        spec.exit_code(403, 'ERROR_NO_GIPAW_INFO_FOUND', message='The pseudos for one or more absorbing elements'
                       ' contain no GIPAW information.')
        spec.output(
            'optimized_structure',
            valid_type=orm.StructureData,
            required=False,
            help='The optimized structure from the ``relax`` process.',
        )
        spec.output(
            'standardized_structure',
            valid_type=orm.StructureData,
            help='The standardized crystal structure used to generate structures for XSpectra sub-processes.',
        )
        spec.output(
            'supercell_structure',
            valid_type=orm.StructureData,
            help='The supercell of ``outputs.standardized_structure`` used to generate structures for'
            ' XSpectra sub-processes.'
        )
        spec.output(
            'symmetry_analysis_data',
            valid_type=orm.Dict,
            help='The output parameters from ``get_xspectra_structures()``.'
        )
        spec.output(
            'parameters_relax',
            valid_type=orm.Dict,
            required=False,
            help='The output_parameters of the relax step.'
        )
        spec.output_namespace(
            'parameters_scf',
            valid_type=orm.Dict,
            required=False,
            dynamic=True,
            help='The output parameters of each ``PwBaseWorkChain`` performed in each ``XspectraCoreWorkChain``.'
        )
        spec.output_namespace(
            'parameters_xspectra',
            valid_type=orm.Dict,
            required=False,
            dynamic=True,
            help='The output dictionaries of each `XspectraCalculation` performed',
        )
        spec.output_namespace(
            'powder_spectra',
            valid_type=orm.XyData,
            required=False,
            dynamic=True,
            help='All the spectra generated by the WorkChain.'
        )
        spec.output_namespace(
            'final_spectra',
            valid_type=orm.XyData,
            dynamic=True,
            help='The fully-resolved spectra for each element'
        )
        # yapf: disable

    @classmethod
    def get_protocol_filepath(cls):
        """Return ``pathlib.Path`` to the ``.yaml`` file that defines the protocols."""
        from importlib_resources import files

        from ..protocols import xspectra as protocols
        return files(protocols) / 'crystal.yaml'

    @classmethod
    def get_builder_from_protocol(
        cls, pw_code, xs_code, upf2plotcore_code, structure, pseudos,
        core_hole_treatments=None, protocol=None, overrides=None, elements_list=None,
        options=None, **kwargs
    ):
        """Return a builder prepopulated with inputs selected according to the chosen protocol.

        :param pw_code: the ``Code`` instance configured for the ``quantumespresso.pw``
            plugin.
        :param xs_code: the ``Code`` instance configured for the
            ``quantumespresso.xspectra`` plugin.
        :param upf2plotcore_code: the AiiDA-Shell ``Code`` instance configured for the
                                  upf2plotcore shell script.
        :param structure: the ``StructureData`` instance to use.
        :param pseudos: the core-hole pseudopotential pairs (ground-state and
                        excited-state) for the elements to be calculated. These must
                        use the mapping of {"element" : {"core_hole" : <upf>,
                                                         "gipaw" : <upf>}}
        :param protocol: the protocol to use. If not specified, the default will be used.
        :param overrides: optional dictionary of inputs to override the defaults of the
                          XspectraWorkChain itself.
        :param kwargs: additional keyword arguments that will be passed to the
            ``get_builder_from_protocol`` of all the sub processes that are called by this
            workchain.
        :return: a process builder instance with all inputs defined ready for launch.
        """

        inputs = cls.get_protocol_inputs(protocol, overrides)

        pw_args = (pw_code, structure, protocol)

        relax = PwRelaxWorkChain.get_builder_from_protocol(
            *pw_args, overrides=inputs.get('relax', None), options=options, **kwargs
        )
        core_scf = PwBaseWorkChain.get_builder_from_protocol(
            *pw_args, overrides=inputs.get('core', {}).get('scf'), options=options, **kwargs
        )
        core_xspectra = XspectraBaseWorkChain.get_protocol_inputs(
            protocol,
            overrides=inputs.get('core', {}).get('xs_prod')
        )

        if options:
            core_xspectra['xspectra']['metadata']['options'] = recursive_merge(
                core_xspectra['xspectra']['metadata']['options'],
                options
            )

        relax.pop('clean_workdir', None)
        relax.pop('structure', None)
        relax.pop('base_final_scf', None)

        abs_atom_marker = orm.Str(inputs['abs_atom_marker'])
        # pylint: disable=no-member
        builder = cls.get_builder()
        builder.relax = relax
        builder.upf2plotcore_code = upf2plotcore_code
        builder.structure = structure
        builder.abs_atom_marker = abs_atom_marker
        builder.clean_workdir = orm.Bool(inputs['clean_workdir'])
        builder.return_all_powder_spectra = orm.Bool(inputs['return_all_powder_spectra'])
        builder.core.scf = core_scf
        builder.core.xs_prod.xspectra.code = xs_code
        builder.core.xs_prod.xspectra.parameters = orm.Dict(core_xspectra['xspectra']['parameters'])
        builder.core.xs_prod.xspectra.metadata = core_xspectra['xspectra'].get('metadata')
        builder.core.xs_prod.kpoints_distance = orm.Float(core_xspectra['kpoints_distance'])
        builder.core.get_powder_spectrum = orm.Bool(True)
        builder.core.abs_atom_marker = abs_atom_marker
        core_hole_pseudos = {}
        gipaw_pseudos = {}
        if elements_list:
            elements_not_present = []
            elements_present = [kind.symbol for kind in structure.kinds]
            for element in elements_list:
                if element not in elements_present:
                    elements_not_present.append(element)
            if len(elements_not_present) > 0:
                raise ValueError(
                    f'The following elements: {elements_not_present} are not present in the'
                    f' structure ({elements_present}) provided.'
                    )
            else:
                builder.elements_list = orm.List(elements_list)
                for element in pseudos:
                    core_hole_pseudos[element] = pseudos[element]['core_hole']
                    gipaw_pseudos[element] = pseudos[element]['gipaw']
        # if no elements list is given, we instead initalise the pseudos dict with all
        # elements in the structure
        else:
            for element in pseudos:
                core_hole_pseudos[element] = pseudos[element]['core_hole']
                gipaw_pseudos[element] = pseudos[element]['gipaw']
        builder.core_hole_pseudos = core_hole_pseudos
        builder.gipaw_pseudos = gipaw_pseudos
        if core_hole_treatments:
            builder.core_hole_treatments = orm.Dict(dict=core_hole_treatments)
        # pylint: enable=no-member
        return builder


    @staticmethod
    def validate_inputs(inputs, _):
        """Validate the inputs before launching the WorkChain."""
        structure = inputs['structure']
        elements_present = [kind.name for kind in structure.kinds]
        abs_atom_marker = inputs['abs_atom_marker'].value
        if abs_atom_marker in elements_present:
            raise ValidationError(
                f'The marker given for the absorbing atom ("{abs_atom_marker}") matches an existing Kind in the '
                f'input structure ({elements_present}).'
            )

        if not inputs['core']['get_powder_spectrum'].value:
            raise ValidationError(
                'The ``get_powder_spectrum`` input for the XspectraCoreWorkChain namespace must be ``True``.'
            )

    def setup(self):
        """Set required context variables."""
        custom_elements_list = self.inputs.get('elements_list', None)
        if not custom_elements_list:
            structure = self.inputs.structure
            self.ctx.elements_list = [kind.symbol for kind in structure.kinds]
        else:
            self.ctx.elements_list = custom_elements_list.get_list()


    def should_run_relax(self):
        """If the 'relax' input namespace was specified, we relax the input structure."""
        return 'relax' in self.inputs

    def run_relax(self):
        """Run the PwRelaxWorkChain to run a relax PwCalculation."""
        inputs = AttributeDict(self.exposed_inputs(PwRelaxWorkChain, namespace='relax'))
        inputs.metadata.call_link_label = 'relax'
        inputs.structure = self.inputs.structure

        running = self.submit(PwRelaxWorkChain, **inputs)

        self.report(f'launching PwRelaxWorkChain<{running.pk}>')

        return ToContext(relax_workchain=running)

    def inspect_relax(self):
        """Verify that the PwRelaxWorkChain finished successfully."""
        workchain = self.ctx.relax_workchain

        if not workchain.is_finished_ok:
            self.report(f'PwRelaxWorkChain failed with exit status {workchain.exit_status}')
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_RELAX

        relaxed_structure = workchain.outputs.output_structure
        self.ctx.relaxed_structure = relaxed_structure
        self.out('optimized_structure', relaxed_structure)

    def get_xspectra_structures(self):
        """Perform symmetry analysis of the relaxed structure and get all marked structures for XSpectra."""
        from aiida_quantumespresso.workflows.functions.get_xspectra_structures import get_xspectra_structures

        elements_list = orm.List(self.ctx.elements_list)

        inputs = {
            'absorbing_elements_list' : elements_list,
            'absorbing_atom_marker' : self.inputs.abs_atom_marker,
            'metadata' : {
                'call_link_label' : 'get_xspectra_structures'
            }
        }
        if 'structure_preparation_settings' in self.inputs:
            optional_cell_prep = self.inputs.structure_preparation_settings
            for key, value in optional_cell_prep.items():
                if isinstance(value, float):
                    node_value = orm.Float(value)
                if isinstance(value, int):
                    node_value = orm.Int(value)
                if isinstance(value, bool):
                    node_value = orm.Bool(value)
                if isinstance(value, str):
                    node_value = orm.Str(value)
                inputs[key] = node_value

        if 'spglib_settings' in self.inputs:
            spglib_settings = self.inputs.spglib_settings
            inputs['spglib_settings'] = spglib_settings
        else:
            spglib_settings = None

        if 'relax' in self.inputs:
            result = get_xspectra_structures(self.ctx.relaxed_structure, **inputs)
        else:
            result = get_xspectra_structures(self.inputs.structure, **inputs)

        supercell = result.pop('supercell')
        out_params = result.pop('output_parameters')
        spacegroup_number = out_params['spacegroup_number']
        if out_params.get_dict()['structure_is_standardized']:
            standardized = result.pop('standardized_structure')
            self.out('standardized_structure', standardized)
        if spacegroup_number in range(1, 75): # trichoric system
            self.ctx.eps_vectors = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
        if spacegroup_number in range(75, 195): # dichoric system
            self.ctx.eps_vectors = [[1., 0., 0.], [0., 0., 1.]]
        if spacegroup_number in range(195, 231): # isochoric system
            self.ctx.eps_vectors = [[1., 0., 0.]]

        structures_to_process = {f'{Key.split("_")[0]}_{Key.split("_")[1]}' : Value for Key, Value in result.items()}
        self.ctx.structures_to_process = structures_to_process
        self.ctx.equivalent_sites_data = out_params['equivalent_sites_data']

        self.out('supercell_structure', supercell)
        self.out('symmetry_analysis_data', out_params)

    def run_upf2plotcore(self):
        """Run the upf2plotcore.sh utility script for each element and return the core-wavefunction data."""

        ShellJob = CalculationFactory('core.shell') # pylint: disable=invalid-name
        elements_list = self.ctx.elements_list

        shelljobs = {}
        for element in elements_list:
            upf = self.inputs.gipaw_pseudos[f'{element}']

            shell_inputs = {}

            shell_inputs['code'] = self.inputs.upf2plotcore_code
            shell_inputs['nodes'] = {'upf': upf}
            shell_inputs['arguments'] = ['upf']
            shell_inputs['metadata'] = {'call_link_label': f'upf2plotcore_{element}'}

            future_shelljob = self.submit(ShellJob, **shell_inputs)
            self.report(f'Launching upf2plotcore.sh for {element}<{future_shelljob.pk}>')
            shelljobs[f'upf2plotcore_{element}'] = future_shelljob

        return ToContext(**shelljobs)


    def inspect_upf2plotcore(self):
        """Check that the outputs from the upf2plotcore step have yielded meaningful results.

        This will simply check that the core wavefunction data returned contains at least
        one core state and return an error if this is not the case.
        """

        labels = self.ctx.elements_list
        for label in labels:
            shelljob_node = self.ctx[f'upf2plotcore_{label}']
            core_wfc_data = shelljob_node.outputs.stdout
            header_line = core_wfc_data.get_content()[:40]
            num_core_states = int(header_line.split(' ')[5])
            if num_core_states == 0:
                return self.exit_codes.ERROR_NO_GIPAW_INFO_FOUND

    def run_all_xspectra_core(self):
        """Call all ``XspectraCoreWorkChain``s required to compute all requested spectra."""

        structures_to_process = self.ctx.structures_to_process
        equivalent_sites_data = self.ctx.equivalent_sites_data
        abs_atom_marker = self.inputs.abs_atom_marker.value

        for site in structures_to_process:
            inputs = AttributeDict(self.exposed_inputs(XspectraCoreWorkChain, namespace='core'))
            structure =structures_to_process[site]
            inputs.structure = structure
            abs_element = equivalent_sites_data[site]['symbol']

            if 'core_hole_treatments' in self.inputs:
                ch_treatments = self.inputs.core_hole_treatments.get_dict()
                ch_treatment = ch_treatments.get(abs_element, 'full')
            else:
                ch_treatment = 'full'

            inputs.metadata.call_link_label = f'{site}_xspectra'
            inputs.eps_vectors = orm.List(list=self.ctx.eps_vectors)
            inputs.core_wfc_data = self.ctx[f'upf2plotcore_{abs_element}'].outputs.stdout

            # Get the given settings for the SCF inputs and then overwrite them with the
            # chosen core-hole approximation, then apply the correct pseudopotential pair
            scf_params = inputs.scf.pw.parameters.get_dict()
            ch_inputs = XspectraCoreWorkChain.get_treatment_inputs(treatment=ch_treatment)
            new_scf_params = recursive_merge(left=scf_params, right=ch_inputs)

            # Set the parameter `xiabs` correctly, now that we know which structure we have
            new_xs_params = inputs.xs_prod.xspectra.parameters.get_dict()
            kinds_present = sorted([kind.name for kind in structure.kinds])
            abs_species_index = kinds_present.index(abs_atom_marker) + 1
            new_xs_params['INPUT_XSPECTRA']['xiabs'] = abs_species_index

            # Set `starting_magnetization` if we are using an XCH approximation, using the
            # absorbing species as a reasonable place for the unpaired electron.
            if ch_treatment == 'xch_smear':
                new_scf_params['SYSTEM'][f'starting_magnetization({abs_species_index})'] = 1

            core_hole_pseudo = self.inputs.core_hole_pseudos[abs_element]
            gipaw_pseudo = self.inputs.gipaw_pseudos[abs_element]
            inputs.scf.pw.pseudos[abs_atom_marker] = core_hole_pseudo
            inputs.scf.pw.pseudos[abs_element] = gipaw_pseudo

            inputs.scf.pw.parameters = orm.Dict(dict=new_scf_params)
            inputs.xs_prod.xspectra.parameters = orm.Dict(dict=new_xs_params)

            inputs = prepare_process_inputs(XspectraCoreWorkChain, inputs)

            future = self.submit(XspectraCoreWorkChain, **inputs)
            self.to_context(xspectra_core_workchains=append_(future))
            self.report(f'launched XspectraCoreWorkChain for {site}<{future.pk}>')

    def inspect_all_xspectra_core(self):
        """Check that all the XspectraCoreWorkChain sub-processes finished sucessfully."""

        labels = self.ctx.structures_to_process.keys()
        work_chain_nodes = self.ctx.xspectra_core_workchains
        failed_work_chains = []
        for work_chain, label in zip(work_chain_nodes, labels):
            if not work_chain.is_finished_ok:
                failed_work_chains.append(work_chain)
                self.report(f'XspectraCoreWorkChain for ({label}) failed with exit status {work_chain.exit_status}')
        if len(failed_work_chains) > 0:
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_XSPECTRA

    def results(self):
        """Compile all output spectra, organise and post-process all computed spectra, and send to outputs."""

        site_labels = self.ctx.structures_to_process.keys()
        work_chain_nodes = self.ctx.xspectra_core_workchains
        spectra_nodes = {label : node.outputs.powder_spectrum for label, node in zip(site_labels, work_chain_nodes)}
        spectra_nodes['metadata'] = {'call_link_label' : 'compile_final_spectra'}

        equivalent_sites_data = self.ctx.equivalent_sites_data
        elements_list = orm.List(list=self.ctx.elements_list)
        final_spectra = get_spectra_by_element(elements_list, equivalent_sites_data, **spectra_nodes)

        self.out('final_spectra', final_spectra)

        if self.inputs.return_all_powder_spectra.value:
            spectra_nodes.pop('metadata', None)
            self.out('powder_spectra', spectra_nodes)

    def on_terminated(self):
        """Clean the working directories of all child calculations if ``clean_workdir=True`` in the inputs."""

        super().on_terminated()

        if self.inputs.clean_workdir.value is False:
            self.report('remote folders will not be cleaned')
            return

        cleaned_calcs = []

        for called_descendant in self.node.called_descendants:
            if isinstance(called_descendant, orm.CalcJobNode):
                try:
                    called_descendant.outputs.remote_folder._clean()  # pylint: disable=protected-access
                    cleaned_calcs.append(called_descendant.pk)
                except (IOError, OSError, KeyError):
                    pass

        if cleaned_calcs:
            self.report(f"cleaned remote folders of calculations: {' '.join(map(str, cleaned_calcs))}")