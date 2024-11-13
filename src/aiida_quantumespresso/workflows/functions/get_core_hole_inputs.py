# -*- coding: utf-8 -*-
"""Utility to generate suitable inputs for a PwCalculation based on a given core-hole treatment setting.

Returns a `dict` object with suitable inputs for a subsequent PwCalculation
"""


# pylint: disable=too-many-statements
def _process_afm_system(**kwargs):
    """Set the magnetic structure correctly for the system with the core-hole present."""
    structure = kwargs.get('structure')
    equivalent_sites_data = kwargs.get('equivalent_sites_data')
    starting_mag = kwargs.get('starting_mag')
    tot_mag = kwargs.get('tot_mag')
    abs_atom_marker = kwargs.get('abs_atom_marker', 'X')
    site_index = kwargs.get('site_index', None)
    treatment = kwargs.get('treatment')
    final_starting_mag = starting_mag.copy()

    # first, figure out which site index we need, if not given
    if not site_index:
        for index, site in enumerate(structure.sites):
            if site.kind_name == abs_atom_marker:
                target_site_index = index
                break
    else:
        target_site_index = site_index

    # then, look up the magnetic moment we should have and modify parameters accordingly
    for value in equivalent_sites_data.values():
        if value['site_index'] == target_site_index:
            target_site_kind = value['kind_name']
            target_site_mag = starting_mag[target_site_kind]

    final_starting_mag[abs_atom_marker] = target_site_mag
    if treatment not in ['full', 'FCH']:
        if target_site_mag > 0:
            tot_mag += 1
        else:
            tot_mag -= 1

    return final_starting_mag, tot_mag


def get_core_hole_inputs(structure, treatment, equivalent_sites_data, parameters, **kwargs):
    """Generate PwCalculation input parameters based on a given core-hole treatment type."""

    # Validity checks needed:
    # - abs_atom_marker is present in structure.kinds
    # - treatment is a valid setting. Valid options:
    #   - FCH, HCH, XCH, full, half, xch_smear, xch_fixed (include older ones for backwards-compatability)
    # - "none" is unnecessary, since the simplest option is just to skip the function call completely.

    abs_atom_marker = kwargs.get('abs_atom_marker', 'X')
    site_index = kwargs.get('site_index', None)
    updated_parameters = parameters.copy()
    starting_mag = parameters['SYSTEM'].get('starting_magnetization', None)
    tot_mag = parameters['SYSTEM'].get('tot_magnetization', None)
    tot_charge = parameters['SYSTEM'].get('tot_charge', 0)
    afm_inputs = {
        'structure': structure,
        'equivalent_sites_data': equivalent_sites_data,
        'starting_mag': starting_mag,
        'tot_mag': tot_mag,
        'abs_atom_marker': abs_atom_marker,
        'site_index': site_index,
        'treatment': treatment
    }

    if starting_mag:  # check for both negative and positive numbers (i.e. is the system FM or AFM?)
        positive_mag = False
        negative_mag = False
        for value in starting_mag.values():
            if value > 0:
                positive_mag = True
            elif value < 0:
                negative_mag = True
            if positive_mag and negative_mag:
                break
        system_afm = positive_mag and negative_mag
    else:
        system_afm = False

    if treatment in ['FCH', 'full']:
        tot_charge += 1
        if starting_mag:
            if system_afm:
                starting_mag, tot_mag = _process_afm_system(**afm_inputs)
            else:
                starting_mag[abs_atom_marker] = 1
    elif treatment in ['HCH', 'half']:
        tot_charge += 0.5
        if starting_mag:
            starting_mag[abs_atom_marker] = 1
    elif treatment in ['XCH', 'xch_fixed', 'xch_smear']:
        if system_afm:
            starting_mag, tot_mag = _process_afm_system(**afm_inputs)
        elif updated_parameters['SYSTEM']['nspin'] == 2:
            starting_mag[abs_atom_marker] = 1
            # if treatment == 'xch_fixed' or updated_parameters['SYSTEM'].get('occupations', None) == 'fixed':
            tot_mag += 1
        else:
            starting_mag = {abs_atom_marker: 1}
            updated_parameters['SYSTEM']['nspin'] = 2
            if treatment == 'xch_fixed' or updated_parameters['SYSTEM'].get('occupations', None) == 'fixed':
                tot_mag += 1

    if tot_charge > 0:
        updated_parameters['SYSTEM']['tot_charge'] = tot_charge
    if 'nspin' in updated_parameters['SYSTEM']:
        if updated_parameters['SYSTEM']['nspin'] == 2:
            updated_parameters['SYSTEM']['starting_magnetization'] = starting_mag
            if tot_mag:
                updated_parameters['SYSTEM']['tot_magnetization'] = tot_mag
            # if starting_mag:

    return updated_parameters
