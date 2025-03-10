# -*- coding: utf-8 -*-
#
# This file is part of HEPData.
# Copyright (C) 2016 CERN.
#
# HEPData is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# HEPData is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HEPData; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, CERN does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

from flask_webpackext import WebpackBundle

dashboard_js = WebpackBundle(
    __name__,
    'assets',
    entry={
        'dc': './node_modules/dc/dc.min.css',
        'hepdata-dashboard-js': './js/hepdata_dashboard.js',
        'hepdata-dashboard-inspire-js': './js/hepdata_dashboard_inspire.js',
        'hepdata-dashboard-manage-scripts-js': './js/hepdata_dashboard_manage_scripts.js',
        'hepdata-dashboard-submissions-vis-js': './js/hepdata_dashboard_submissions_vis.js'
    },
    dependencies={
        "crossfilter": "1.3.12",
        "d3": "~3.5.12",
        "d3-tip": "~0.6.7",
        "dc": "~2.0.0",
        "typeahead.js": "0.11.1"
    }
)
