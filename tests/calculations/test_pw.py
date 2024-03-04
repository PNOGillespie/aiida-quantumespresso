# -*- coding: utf-8 -*-
"""Tests for the `PwCalculation` class."""

from aiida import orm
from aiida.common import datastructures
from aiida.common.exceptions import InputValidationError
from aiida.common.warnings import AiidaDeprecationWarning
import pytest

from aiida_quantumespresso.calculations.helpers import QEInputValidationError
from aiida_quantumespresso.utils.resources import get_default_options


def test_pw_default(fixture_sandbox, generate_calc_job, generate_inputs_pw, file_regression):
    """Test a default `PwCalculation`."""
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    calc_info = generate_calc_job(fixture_sandbox, entry_point_name, inputs)
    upf = inputs['pseudos']['Si']

    cmdline_params = ['-in', 'aiida.in']
    local_copy_list = [(upf.uuid, upf.filename, './pseudo/Si.upf')]
    retrieve_list = ['aiida.out', './out/aiida.save/data-file-schema.xml', './out/aiida.save/data-file.xml', 'CRASH']
    retrieve_temporary_list = [['./out/aiida.save/K*[0-9]/eigenval*.xml', '.', 2]]

    # Check the attributes of the returned `CalcInfo`
    assert isinstance(calc_info, datastructures.CalcInfo)
    assert isinstance(calc_info.codes_info[0], datastructures.CodeInfo)
    assert sorted(calc_info.codes_info[0].cmdline_params) == cmdline_params
    assert sorted(calc_info.local_copy_list) == sorted(local_copy_list)
    assert sorted(calc_info.retrieve_list) == sorted(retrieve_list)
    assert sorted(calc_info.retrieve_temporary_list) == sorted(retrieve_temporary_list)
    assert sorted(calc_info.remote_symlink_list) == sorted([])

    with fixture_sandbox.open('aiida.in') as handle:
        input_written = handle.read()

    # Checks on the files written to the sandbox folder as raw input
    assert sorted(fixture_sandbox.get_content_list()) == sorted(['aiida.in', 'pseudo', 'out'])
    file_regression.check(input_written, encoding='utf-8', extension='.in')


def test_pw_ibrav(
    fixture_sandbox, generate_calc_job, fixture_code, generate_kpoints_mesh, generate_upf_data, file_regression
):
    """Test a `PwCalculation` where `ibrav` is explicitly specified."""
    entry_point_name = 'quantumespresso.pw'

    parameters = {'CONTROL': {'calculation': 'scf'}, 'SYSTEM': {'ecutrho': 240.0, 'ecutwfc': 30.0, 'ibrav': 2}}

    # The structure needs to be rotated in the same way QE does it for ibrav=2.
    param = 5.43
    cell = [[-param / 2., 0, param / 2.], [0, param / 2., param / 2.], [-param / 2., param / 2., 0]]
    structure = orm.StructureData(cell=cell)
    structure.append_atom(position=(0., 0., 0.), symbols='Si', name='Si')
    structure.append_atom(position=(param / 4., param / 4., param / 4.), symbols='Si', name='Si')

    upf = generate_upf_data('Si')
    inputs = {
        'code': fixture_code(entry_point_name),
        'structure': structure,
        'kpoints': generate_kpoints_mesh(2),
        'parameters': orm.Dict(parameters),
        'pseudos': {
            'Si': upf
        },
        'metadata': {
            'options': get_default_options()
        }
    }

    calc_info = generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    cmdline_params = ['-in', 'aiida.in']
    local_copy_list = [(upf.uuid, upf.filename, './pseudo/Si.upf')]
    retrieve_list = ['aiida.out', './out/aiida.save/data-file-schema.xml', './out/aiida.save/data-file.xml', 'CRASH']
    retrieve_temporary_list = [['./out/aiida.save/K*[0-9]/eigenval*.xml', '.', 2]]

    # Check the attributes of the returned `CalcInfo`
    assert isinstance(calc_info, datastructures.CalcInfo)
    assert isinstance(calc_info.codes_info[0], datastructures.CodeInfo)
    assert sorted(calc_info.codes_info[0].cmdline_params) == sorted(cmdline_params)
    assert sorted(calc_info.local_copy_list) == sorted(local_copy_list)
    assert sorted(calc_info.retrieve_list) == sorted(retrieve_list)
    assert sorted(calc_info.retrieve_temporary_list) == sorted(retrieve_temporary_list)
    assert sorted(calc_info.remote_symlink_list) == sorted([])

    with fixture_sandbox.open('aiida.in') as handle:
        input_written = handle.read()

    # Checks on the files written to the sandbox folder as raw input
    assert sorted(fixture_sandbox.get_content_list()) == sorted(['aiida.in', 'pseudo', 'out'])
    file_regression.check(input_written, encoding='utf-8', extension='.in')


def test_pw_wrong_ibrav(fixture_sandbox, generate_calc_job, fixture_code, generate_kpoints_mesh, generate_upf_data):
    """Test that a `PwCalculation` with an incorrect `ibrav` raises."""
    entry_point_name = 'quantumespresso.pw'

    parameters = {'CONTROL': {'calculation': 'scf'}, 'SYSTEM': {'ecutrho': 240.0, 'ecutwfc': 30.0, 'ibrav': 2}}

    # Here we use the wrong order of unit cell vectors on purpose.
    param = 5.43
    cell = [[0, param / 2., param / 2.], [-param / 2., 0, param / 2.], [-param / 2., param / 2., 0]]
    structure = orm.StructureData(cell=cell)
    structure.append_atom(position=(0., 0., 0.), symbols='Si', name='Si')
    structure.append_atom(position=(param / 4., param / 4., param / 4.), symbols='Si', name='Si')

    upf = generate_upf_data('Si')
    inputs = {
        'code': fixture_code(entry_point_name),
        'structure': structure,
        'kpoints': generate_kpoints_mesh(2),
        'parameters': orm.Dict(parameters),
        'pseudos': {
            'Si': upf
        },
        'metadata': {
            'options': get_default_options()
        }
    }

    with pytest.raises(QEInputValidationError):
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)


def test_pw_ibrav_tol(fixture_sandbox, generate_calc_job, fixture_code, generate_kpoints_mesh, generate_upf_data):
    """Test that `IBRAV_TOLERANCE` controls the tolerance when checking cell consistency."""
    entry_point_name = 'quantumespresso.pw'

    parameters = {'CONTROL': {'calculation': 'scf'}, 'SYSTEM': {'ecutrho': 240.0, 'ecutwfc': 30.0, 'ibrav': 2}}

    # The structure needs to be rotated in the same way QE does it for ibrav=2.
    param = 5.43
    eps = 0.1
    cell = [[-param / 2., eps, param / 2.], [-eps, param / 2. + eps, param / 2.], [-param / 2., param / 2., 0]]
    structure = orm.StructureData(cell=cell)
    structure.append_atom(position=(0., 0., 0.), symbols='Si', name='Si')
    structure.append_atom(position=(param / 4., param / 4., param / 4.), symbols='Si', name='Si')

    upf = generate_upf_data('Si')
    inputs = {
        'code': fixture_code(entry_point_name),
        'structure': structure,
        'kpoints': generate_kpoints_mesh(2),
        'parameters': orm.Dict(parameters),
        'pseudos': {
            'Si': upf
        },
        'metadata': {
            'options': get_default_options()
        },
    }
    # Without adjusting the tolerance, the check fails.
    with pytest.raises(QEInputValidationError):
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    # After adjusting the tolerance, the input validation no longer fails.
    inputs['settings'] = orm.Dict({'ibrav_cell_tolerance': eps})
    generate_calc_job(fixture_sandbox, entry_point_name, inputs)


def test_pw_parallelization_inputs(fixture_sandbox, generate_calc_job, generate_inputs_pw):
    """Test that the parallelization settings are set correctly in the commandline params."""
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs['parallelization'] = orm.Dict({'npool': 4, 'nband': 2, 'ntg': 3, 'ndiag': 12})
    calc_info = generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    cmdline_params = ['-npool', '4', '-nband', '2', '-ntg', '3', '-ndiag', '12', '-in', 'aiida.in']

    # Check that the command-line parameters are as expected.
    assert calc_info.codes_info[0].cmdline_params == cmdline_params


@pytest.mark.parametrize('flag_name', ['npool', 'nk', 'nband', 'nb', 'ntg', 'nt', 'northo', 'ndiag', 'nd'])
def test_pw_parallelization_deprecation(fixture_sandbox, generate_calc_job, generate_inputs_pw, flag_name):
    """Test the deprecation warning on specifying parallelization flags manually.

    Test that passing parallelization flags in the `settings['CMDLINE']
    emits an `AiidaDeprecationWarning`.
    """
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    extra_cmdline_args = [f'-{flag_name}', '2']
    inputs['settings'] = orm.Dict({'CMDLINE': extra_cmdline_args})
    with pytest.warns(AiidaDeprecationWarning) as captured_warnings:
        calc_info = generate_calc_job(fixture_sandbox, entry_point_name, inputs)
        assert calc_info.codes_info[0].cmdline_params == extra_cmdline_args + ['-in', 'aiida.in']
    assert any('parallelization flags' in str(warning.message) for warning in captured_warnings.list)


def test_pw_parallelization_conflict_error(fixture_sandbox, generate_calc_job, generate_inputs_pw):
    """Test conflict between `settings['CMDLINE']` and `parallelization`.

    Test that passing the same parallelization flag (modulo aliases)
    manually in `settings['CMDLINE']` and in the `parallelization`
    input raises an `InputValidationError`.
    """
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    extra_cmdline_args = ['-nk', '2']
    inputs['settings'] = orm.Dict({'CMDLINE': extra_cmdline_args})
    inputs['parallelization'] = orm.Dict({'npool': 2})
    with pytest.raises(InputValidationError) as exc:
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)
    assert 'conflicts' in str(exc.value)


def test_pw_parallelization_incorrect_flag(fixture_sandbox, generate_calc_job, generate_inputs_pw):
    """Test that passing a non-existing parallelization flag raises.

    Test that specifying an non-existing parallelization flag in
    the `parallelization` `Dict` raises a `ValueError`.
    """
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs['parallelization'] = orm.Dict({'invalid_flag_name': 2})
    with pytest.raises(ValueError) as exc:
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)
    assert 'Unknown' in str(exc.value)


def test_pw_parallelization_incorrect_value(fixture_sandbox, generate_calc_job, generate_inputs_pw):
    """Test that passing a non-integer parallelization flag raises.

    Test that specifying an non-integer parallelization flag value in
    the `parallelization` `Dict` raises a `ValueError`.
    """
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs['parallelization'] = orm.Dict({'npool': 2.2})
    with pytest.raises(ValueError) as exc:
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)
    assert 'integer' in str(exc.value)


def test_pw_parallelization_duplicate_cmdline_flag(fixture_sandbox, generate_calc_job, generate_inputs_pw):
    """Test that passing two different aliases to the same parallelization flag raises."""
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs['settings'] = orm.Dict({'CMDLINE': ['-nk', '2', '-npools', '2']})
    with pytest.raises(InputValidationError) as exc:
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)
    assert 'Conflicting' in str(exc.value)


def test_pw_validate_pseudos_missing(fixture_sandbox, generate_calc_job, generate_inputs_pw):
    """Test the validation for the ``pseudos`` port when it is not specified at all."""
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs.pop('pseudos', None)

    with pytest.raises(ValueError, match=r'required value was not provided for the `pseudos` namespace\.'):
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)


def test_pw_validate_pseudos_incorrect_kinds(fixture_sandbox, generate_calc_job, generate_inputs_pw, generate_upf_data):
    """Test the validation for the ``pseudos`` port when the kinds do not match that of the structure."""
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs['pseudos'] = {'X': generate_upf_data('X')}

    with pytest.raises(ValueError, match=r'The `pseudos` specified and structure kinds do not match:.*'):
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)


@pytest.mark.parametrize('calculation', ['scf', 'relax', 'vc-relax'])
def test_pw_validate_inputs_restart_base(
    fixture_sandbox, generate_calc_job, generate_inputs_pw, fixture_localhost, generate_remote_data, calculation
):
    """Test the input validation of restart settings for the ``PwCalculation``."""
    remote_data = generate_remote_data(computer=fixture_localhost, remote_path='/path/to/remote')
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    parameters = inputs['parameters'].get_dict()
    parameters['CONTROL']['calculation'] = calculation

    # Add `parent_folder` but no restart tags -> warning
    inputs['parent_folder'] = remote_data
    with pytest.warns(UserWarning, match='`parent_folder` input was provided for the'):
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    # Set `restart_mode` to `'restart'` -> no warning
    parameters['CONTROL']['restart_mode'] = 'restart'
    inputs['parameters'] = orm.Dict(parameters)
    with pytest.warns(None) as warnings:
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)
    assert len([w for w in warnings.list if w.category is UserWarning]) == 0, [w.message for w in warnings.list]
    parameters['CONTROL'].pop('restart_mode')

    # Set `startingwfc` or `startingpot` to `'file'` -> no warning
    for restart_setting in ('startingpot', 'startingwfc'):
        parameters['ELECTRONS'][restart_setting] = 'file'
        inputs['parameters'] = orm.Dict(parameters)
        with pytest.warns(None) as warnings:
            generate_calc_job(fixture_sandbox, entry_point_name, inputs)
        assert len([w for w in warnings.list if w.category is UserWarning]) == 0
        parameters['ELECTRONS'].pop(restart_setting)


@pytest.mark.parametrize('calculation', ['nscf', 'bands'])
def test_pw_validate_inputs_restart_nscf(
    fixture_sandbox, generate_calc_job, generate_inputs_pw, fixture_localhost, generate_remote_data, calculation
):
    """Test the input validation of restart settings for the ``PwCalculation``."""
    remote_data = generate_remote_data(computer=fixture_localhost, remote_path='/path/to/remote')
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    parameters = inputs['parameters'].get_dict()
    parameters['CONTROL']['calculation'] = calculation

    # No parent_folder -> warn
    inputs['parameters'] = orm.Dict(parameters)
    with pytest.warns(Warning, match='`parent_folder` not provided for `.*` calculation.'):
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    # Parent_folder + defaults -> works
    inputs['parent_folder'] = remote_data
    generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    # Set `restart_mode` to `'restart'` -> works
    parameters['CONTROL']['restart_mode'] = 'restart'
    inputs['parameters'] = orm.Dict(parameters)
    generate_calc_job(fixture_sandbox, entry_point_name, inputs)


def test_fixed_coords(fixture_sandbox, generate_calc_job, generate_inputs_pw, file_regression):
    """Test a ``PwCalculation`` where the ``fixed_coords`` setting was provided."""
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs['settings'] = orm.Dict(dict={'FIXED_COORDS': [[True, True, False], [False, True, False]]})
    generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    with fixture_sandbox.open('aiida.in') as handle:
        input_written = handle.read()

    file_regression.check(input_written, encoding='utf-8', extension='.in')


@pytest.mark.parametrize(['fixed_coords', 'error_message'], [
    ([[True, True], [False, True]], 'The `fixed_coords` setting must be a list of lists with length 3.'),
    ([[True, True, 1], [False, True, False]
      ], 'All elements in the `fixed_coords` setting lists must be either `True` or `False`.'),
    ([
        [True, True, False],
    ], 'Input structure has 2 sites, but fixed_coords has length 1'),
])
def test_fixed_coords_validation(fixture_sandbox, generate_calc_job, generate_inputs_pw, fixed_coords, error_message):
    """Test the validation of the ``fixed_coords`` setting for the ``PwCalculation``."""
    entry_point_name = 'quantumespresso.pw'

    inputs = generate_inputs_pw()
    inputs['settings'] = orm.Dict(dict={'FIXED_COORDS': fixed_coords})
    with pytest.raises(ValueError, match=error_message):
        generate_calc_job(fixture_sandbox, entry_point_name, inputs)
