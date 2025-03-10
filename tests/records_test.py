#
# This file is part of HEPData.
# Copyright (C) 2015 CERN.
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

"""HEPData records test cases."""
from io import open, StringIO
import os
import re
from time import sleep
import yaml
import shutil
import tempfile
import datetime

from flask_login import login_user
from invenio_accounts.models import User
from invenio_db import db
import pytest
from werkzeug.datastructures import FileStorage
import requests_mock

from hepdata.modules.permissions.models import SubmissionParticipant
from hepdata.modules.records.api import process_payload, process_zip_archive, \
    move_files, get_all_ids, has_upload_permissions, \
    has_coordinator_permissions, create_new_version
from hepdata.modules.records.utils.submission import get_or_create_hepsubmission, process_submission_directory, do_finalise, unload_submission
from hepdata.modules.records.utils.common import get_record_by_id, get_record_contents
from hepdata.modules.records.utils.data_processing_utils import generate_table_structure
from hepdata.modules.records.utils.data_files import get_data_path_for_record
from hepdata.modules.records.utils.users import get_coordinators_in_system, has_role
from hepdata.modules.records.utils.workflow import update_record, create_record
from hepdata.modules.records.views import set_data_review_status
from hepdata.modules.submission.models import HEPSubmission, DataReview, \
    DataSubmission
from hepdata.modules.submission.views import process_submission_payload
from hepdata.modules.submission.api import get_latest_hepsubmission
from tests.conftest import TEST_EMAIL
from hepdata.modules.records.utils.records_update_utils import get_inspire_records_updated_since, \
    get_inspire_records_updated_on, update_record_info, RECORDS_PER_PAGE
from hepdata.modules.inspire_api.views import get_inspire_record_information
from hepdata.config import CFG_TMPDIR


def test_record_creation(app):
    """___test_record_creation___"""
    with app.app_context():
        record_information = create_record({'journal_info': 'Phys. Letts', 'title': 'My Journal Paper'})

        assert (record_information['recid'])
        assert (record_information['uuid'])
        assert (record_information['title'] == 'My Journal Paper')


def test_record_update(app):
    """___test_record_update___"""
    with app.app_context():
        record_information = create_record({'journal_info': 'Phys. Letts', 'title': 'My Journal Paper'})

        record = get_record_by_id(record_information['recid'])
        assert (record['title'] == 'My Journal Paper')
        assert (record['journal_info'] == 'Phys. Letts')
        update_record(record_information['recid'], {'journal_info': 'test'})

        updated_record = get_record_by_id(record_information['recid'])
        assert (updated_record['journal_info'] == 'test')


def test_get_record(app, client):
    with app.app_context():
        content = client.get('/record/1')
        assert (content is not None)


def test_get_record_contents(app, load_default_data, identifiers):
    # Status finished - should use ES to get results
    record1 = get_record_contents(1, status='finished')
    for key in ["inspire_id", "title"]:
        assert (record1[key] == identifiers[0][key])

    # Status todo - should use DB for result
    record2 = get_record_contents(1, status='todo')
    # DB returns data from an Invenio RecordMetadata obj so has fewer fields
    # than the ES dict
    for key in record2.keys():
        if key == 'last_updated':
            # Date format is slightly different for DB vs ES
            assert(record2[key].replace(' ', 'T') == record1[key])
        else:
            assert(record2[key] == record1[key])

    record3 = get_record_contents(1)
    assert (record3 == record1)

    assert(get_record_contents(9999999) is None)


def test_get_coordinators(app):
    with app.app_context():
        coordinators = get_coordinators_in_system()
        assert (len(coordinators) == 1)


def test_has_role(app):
    with app.app_context():
        user = User.query.filter_by(email=TEST_EMAIL).first()
        assert (user is not None)
        assert (has_role(user, 'coordinator'))
        assert (not has_role(user, 'awesome'))


def test_data_processing(app):
    base_dir = os.path.dirname(os.path.realpath(__file__))

    data = yaml.load(open(os.path.join(base_dir, 'test_data/data_table.yaml'), 'rt'), Loader=yaml.CSafeLoader)

    assert ('independent_variables' in data)
    assert ('dependent_variables' in data)

    assert (len(data['independent_variables']) == 1)
    assert (len(data['independent_variables'][0]['values']) == 3)

    assert (len(data['dependent_variables']) == 1)
    assert (len(data['dependent_variables'][0]['values']) == 3)

    data["name"] = 'test'
    data["title"] = 'test'
    data["keywords"] = None
    data["doi"] = 'doi/10.2342'
    data["location"] = 'Data from Figure 2 of preprint'
    data["review"] = []
    data["associated_files"] = []

    table_structure = generate_table_structure(data)

    assert(table_structure["x_count"] == 1)
    assert(len(table_structure["headers"]) == 2)
    assert(len(table_structure["qualifiers"]) == 2)


def test_upload_valid_file(app):
    # Test uploading and processing a file for a record
    with app.app_context():
        base_dir = os.path.dirname(os.path.realpath(__file__))

        for i, status in enumerate(["todo", "sandbox"]):
            user = User.query.first()
            login_user(user)

            recid = f'12345{i}'
            get_or_create_hepsubmission(recid, 1, status=status)

            hepdata_submission = HEPSubmission.query.filter_by(
                publication_recid=recid).first()
            assert(hepdata_submission is not None)
            assert(hepdata_submission.data_abstract is None)
            assert(hepdata_submission.created < hepdata_submission.last_updated)
            assert(hepdata_submission.version == 1)
            assert(hepdata_submission.overall_status == status)

            with open(os.path.join(base_dir, 'test_data/TestHEPSubmission.zip'), "rb") as stream:
                test_file = FileStorage(
                    stream=stream,
                    filename="TestHEPSubmission.zip"
                )
                response = process_payload(recid, test_file, '/test_redirect_url', synchronous=True)

            assert(response.json == {'url': '/test_redirect_url'})

            # Check the submission has been updated
            hepdata_submission = HEPSubmission.query.filter_by(
                publication_recid=recid).first()
            assert(hepdata_submission.data_abstract.startswith('CERN-LHC.  Measurements of the cross section  for ZZ production'))
            assert(hepdata_submission.created < hepdata_submission.last_updated)
            assert(hepdata_submission.version == 1)
            assert(hepdata_submission.overall_status == status)

            # Set the status to finished and try again, to check versioning
            if status == "todo":
                hepdata_submission.overall_status = 'finished'
                db.session.add(hepdata_submission)

            # Sleep before uploading new version to avoid dir name conflict
            sleep(1)

            # Refresh user
            user = User.query.first()
            login_user(user)

            # Upload a new version
            with open(os.path.join(base_dir, 'test_data/TestHEPSubmission.zip'), "rb") as stream:
                test_file = FileStorage(
                    stream=stream,
                    filename="TestHEPSubmission.zip"
                )
                process_payload(recid, test_file, '/test_redirect_url', synchronous=True)

            # Check the submission has been updated (overridden for a sandbox;
            # new version for normal submission)
            expected_versions = 2 if status == "todo" else 1
            hepdata_submissions = HEPSubmission.query.filter_by(
                publication_recid=recid).order_by(HEPSubmission.last_updated).all()
            assert(len(hepdata_submissions) == expected_versions)
            assert(hepdata_submissions[0].version == 1)

            if status == "todo":
                assert(hepdata_submissions[0].overall_status == 'finished')

            assert(hepdata_submissions[-1].data_abstract.startswith('CERN-LHC.  Measurements of the cross section  for ZZ production'))
            assert(hepdata_submissions[-1].version == expected_versions)
            assert(hepdata_submissions[-1].overall_status == status)

            # Check that there are the expected number of subdirectories and
            # zip files under the record's main path
            # For status = 'todo' (standard submission) there will be 1 file
            # and 1 dir for each of 2 versions; for the sandbox submission
            # there will just be 1 file and 1 dir.
            directory = get_data_path_for_record(hepdata_submission.publication_recid)
            assert(os.path.exists(directory))
            filepaths = os.listdir(directory)
            assert(len(filepaths) == 2*expected_versions)

            dir_count = 0
            file_count = 0
            for path in filepaths:
                if os.path.isdir(os.path.join(directory, path)):
                    dir_count += 1
                    assert(re.match(r"\d{10}", path) is not None)
                else:
                    file_count += 1
                    assert(re.match(r"HEPData-%s-v[12]-yaml.zip" % recid, path) is not None)

            assert(dir_count == expected_versions)
            assert(file_count == expected_versions)

            if status == "todo":
                # Delete the v2 submission and check db and v2 files have been removed
                unload_submission(hepdata_submission.publication_recid, version=2)

                hepdata_submissions = HEPSubmission.query.filter_by(
                    publication_recid=recid).order_by(HEPSubmission.last_updated).all()
                assert(len(hepdata_submissions) == 1)
                assert(hepdata_submissions[0].version == 1)
                assert(hepdata_submissions[0].overall_status == 'finished')

                filepaths = os.listdir(directory)
                assert(len(filepaths) == 2)
                assert(f"HEPData-12345{i}-v1-yaml.zip" in filepaths)

            # Delete the submission and check everything has been removed
            unload_submission(hepdata_submission.publication_recid, version=1)

            hepdata_submissions = HEPSubmission.query.filter_by(
                publication_recid=recid).order_by(HEPSubmission.last_updated).all()
            assert(len(hepdata_submissions) == 0)

            assert(not os.path.exists(directory))


def test_upload_valid_file_yaml_gz(app):
    # Test uploading and processing a file for a record
    with app.app_context():
        base_dir = os.path.dirname(os.path.realpath(__file__))
        user = User.query.first()
        login_user(user)

        recid = '1512299'
        get_or_create_hepsubmission(recid, 1)

        hepdata_submission = HEPSubmission.query.filter_by(
            publication_recid=recid).first()
        assert(hepdata_submission is not None)
        assert(hepdata_submission.data_abstract is None)
        assert(hepdata_submission.created < hepdata_submission.last_updated)
        assert(hepdata_submission.version == 1)
        assert(hepdata_submission.overall_status == 'todo')

        with open(os.path.join(base_dir, 'test_data/1512299.yaml.gz'), "rb") as stream:
            test_file = FileStorage(
                stream=stream,
                filename="1512299.yaml.gz"
            )
            response = process_payload(recid, test_file, '/test_redirect_url', synchronous=True)

        assert(response.json == {'url': '/test_redirect_url'})

        # Check the submission has been updated
        hepdata_submission = HEPSubmission.query.filter_by(
            publication_recid=recid).first()
        assert(hepdata_submission.data_abstract.startswith('Unfolded differential decay rates of four kinematic variables'))
        assert(hepdata_submission.created < hepdata_submission.last_updated)
        assert(hepdata_submission.version == 1)
        assert(hepdata_submission.overall_status == 'todo')

        # Set the status to finished and try again, to check versioning
        hepdata_submission.overall_status = 'finished'
        db.session.add(hepdata_submission)

        # Refresh user
        user = User.query.first()
        login_user(user)

        with open(os.path.join(base_dir, 'test_data/1512299.yaml.gz'), "rb") as stream:
            test_file = FileStorage(
                stream=stream,
                filename="1512299.yaml.gz"
            )
            process_payload(recid, test_file, '/test_redirect_url', synchronous=True)

        # Check the submission has been updated
        hepdata_submissions = HEPSubmission.query.filter_by(
            publication_recid=recid).order_by(HEPSubmission.last_updated).all()
        assert(len(hepdata_submissions) == 2)
        assert(hepdata_submissions[0].version == 1)
        assert(hepdata_submissions[0].overall_status == 'finished')
        assert(hepdata_submissions[1].data_abstract.startswith('Unfolded differential decay rates of four kinematic variables'))
        assert(hepdata_submissions[1].version == 2)
        assert(hepdata_submissions[1].overall_status == 'todo')


def test_upload_invalid_file(app):
    # Test uploading an invalid file
    with app.app_context():
        user = User.query.first()
        login_user(user)

        recid = '12345'
        get_or_create_hepsubmission(recid, 1)

        with StringIO("test") as stream:
            test_file = FileStorage(
                stream=stream,
                filename="test.txt"
            )
            response, code = process_payload(recid, test_file, '/test_redirect_url', synchronous=True)

        assert(code == 400)
        assert(response.json == {
            'message': 'You must upload a .zip, .tar, .tar.gz or .tgz file'
            ' (or a .oldhepdata or single .yaml or .yaml.gz file).'
        })


def test_upload_max_size(app):
    # Test uploading a file with size greater than UPLOAD_MAX_SIZE
    app.config.update({'UPLOAD_MAX_SIZE': 1000000})
    with app.app_context():
        user = User.query.first()
        login_user(user)

        recid = '12345'
        get_or_create_hepsubmission(recid, 1)

        base_dir = os.path.dirname(os.path.realpath(__file__))
        with open(os.path.join(base_dir, 'test_data/TestHEPSubmission.zip'), "rb") as stream:
            test_file = FileStorage(stream=stream, filename="TestHEPSubmission.zip")
            response, code = process_payload(recid, test_file, '/test_redirect_url', synchronous=True)

        assert(code == 413)
        assert(response.json == {
            'message': 'TestHEPSubmission.zip too large (1818265 bytes > 1000000 bytes)'
        })


def test_has_upload_permissions(app):
    # Test uploader permissions
    with app.app_context():
        # Create a record
        recid = '12345'
        get_or_create_hepsubmission(recid, 1)

        # Check admin user has upload permissions to new record
        admin_user = user = User.query.first()
        assert has_upload_permissions(recid, admin_user)

        # Create a user who is not admin and not associated with a record
        user = User(email='testuser@hepdata.com', password='hello', active=True)
        db.session.add(user)
        db.session.commit()
        login_user(user)

        assert not has_upload_permissions(recid, user)

        # Add the user as an uploader but not primary - should not be allowed
        submission_participant = SubmissionParticipant(
            user_account=user.id, publication_recid=recid,
            email=user.email, role='uploader')
        db.session.add(submission_participant)
        db.session.commit()

        assert not has_upload_permissions(recid, user)

        # Make the participant primary uploader - should now work
        submission_participant.status = 'primary'
        db.session.add(submission_participant)
        db.session.commit()

        assert has_upload_permissions(recid, user)


def test_has_coordinator_permissions(app):
    # Test coordinator permissions
    with app.app_context():
        recid = '12345'
        hepsubmission = get_or_create_hepsubmission(recid, 1)

        # Check admin user has coordinator permissions to new record
        admin_user = user = User.query.first()
        assert has_coordinator_permissions(recid, admin_user)

        # Create a user who is not admin and not associated with a record
        user = User(email='testuser@hepdata.com', password='hello', active=True)
        db.session.add(user)
        db.session.commit()
        login_user(user)

        assert not has_coordinator_permissions(recid, user)

        # Add the user as an uploader - should not have permission
        submission_participant = SubmissionParticipant(
            user_account=user.id, publication_recid=recid,
            email=user.email, role='uploader')
        db.session.add(submission_participant)
        db.session.commit()

        assert not has_coordinator_permissions(recid, user)

        # Modify record to add this user as coordinator - should now work
        hepsubmission.coordinator = user.get_id()
        db.session.add(hepsubmission)
        db.session.commit()

        assert has_coordinator_permissions(recid, user)


def test_process_zip_archive_invalid(app):
    # Test uploading a zip containing broken symlinks
    base_dir = os.path.dirname(os.path.realpath(__file__))
    file_path = os.path.join(base_dir, 'test_data/submission_invalid_symlink.tgz')
    tmp_path = tempfile.mkdtemp(dir=CFG_TMPDIR)
    shutil.copy2(file_path, tmp_path)
    tmp_file_path = os.path.join(tmp_path, 'submission_invalid_symlink.tgz')
    errors = process_zip_archive(tmp_file_path, 1)
    assert("Exceptions when copying files" in errors)
    assert(len(errors["Exceptions when copying files"]) == 1)
    assert(errors["Exceptions when copying files"][0].get("level") == "error")
    assert(errors["Exceptions when copying files"][0].get("message")
           == "Invalid file TestHEPSubmissionInvalidSymlink/invalid_file_name.txt: "
           "[Errno 2] No such file or directory: "
           "'TestHEPSubmissionInvalidSymlink/invalid_file_name.txt'"
           )
    shutil.rmtree(tmp_path)

    # Test uploading an invalid tarfile (real example: user ran 'tar -czvf' then 'gzip')
    file_path = os.path.join(base_dir, 'test_data/submission_invalid_tarfile.tgz.gz')
    tmp_path = tempfile.mkdtemp(dir=CFG_TMPDIR)
    shutil.copy2(file_path, tmp_path)
    tmp_file_path = os.path.join(tmp_path, 'submission_invalid_tarfile.tgz.gz')
    errors = process_zip_archive(tmp_file_path, 1)
    assert ("Archive file extractor" in errors)
    assert (len(errors["Archive file extractor"]) == 1)
    assert (errors["Archive file extractor"][0].get("level") == "error")
    assert (errors["Archive file extractor"][0].get("message")
            == "{} is not a valid zip or tar archive file.".format(tmp_file_path))
    shutil.rmtree(tmp_path)


def test_move_files_invalid_path():
    errors = move_files('this_is_not_a_real_path', tempfile.mkdtemp(dir=CFG_TMPDIR))
    assert("Exceptions when copying files" in errors)
    assert(len(errors["Exceptions when copying files"]) == 1)
    assert(errors["Exceptions when copying files"][0].get("level") == "error")
    assert(errors["Exceptions when copying files"][0].get("message")
           == "[Errno 2] No such file or directory: 'this_is_not_a_real_path'"
           )


def test_get_updated_records_since_date(app):
    ids_on = get_inspire_records_updated_on(0)
    ids_on += get_inspire_records_updated_on(1)
    ids_on += get_inspire_records_updated_on(2)
    ids_on += get_inspire_records_updated_on(3)
    ids_since = get_inspire_records_updated_since(3)
    assert set(ids_on) == set(ids_since)


def test_get_updated_records_on_date(app):
    test_date = '2021-06-29'
    mock_url = 'https://inspirehep.net/api/literature?sort=mostrecent'
    mock_url += '&size={0}&page=1&fields=control_number'.format(RECORDS_PER_PAGE)
    mock_url += '&q=external_system_identifiers.schema%3AHEPData%20and%20du%3A{}'.format(test_date)
    mock_json = {'hits': {'total': 2, 'hits': [{'id': '1234567'}, {'id': '890123'}]}}
    # Use requests_mock to mock the response from inspirehep.net.
    with requests_mock.Mocker(real_http=True) as mock:
        mock.get(mock_url, json=mock_json, complete_qs=True)
        ids = get_inspire_records_updated_on(test_date)
    assert ids == ['1234567', '890123']


def test_update_record_info(app):
    """Test update of publication information from INSPIRE."""
    assert update_record_info(None) == 'Inspire ID is None'  # case where Inspire ID is None
    for inspire_id in ('1311487', '19999999'):  # check both a valid and invalid Inspire ID
        assert update_record_info(inspire_id) == 'No HEPData submission'  # before creation of HEPSubmission object
        submission = process_submission_payload(
            inspire_id=inspire_id, submitter_id=1,
            reviewer={'name': 'Reviewer', 'email': 'reviewer@hepdata.net'},
            uploader={'name': 'Uploader', 'email': 'uploader@hepdata.net'},
            send_upload_email=False
        )

        # Process the files to create DataSubmission tables in the DB.
        base_dir = os.path.dirname(os.path.realpath(__file__))
        directory = os.path.join(base_dir, 'test_data/test_submission')
        tmp_path = os.path.join(tempfile.mkdtemp(dir=CFG_TMPDIR), 'test_submission')
        shutil.copytree(directory, tmp_path)
        process_submission_directory(tmp_path, os.path.join(tmp_path, 'submission.yaml'),
                                     submission.publication_recid)
        do_finalise(submission.publication_recid, force_finalise=True, convert=False)

        if inspire_id == '19999999':
            assert update_record_info(inspire_id) == 'Invalid Inspire ID'
        else:

            # First change the publication information to that of a different record.
            different_inspire_record_information, status = get_inspire_record_information('1650066')
            assert status == 'success'
            hep_submission = get_latest_hepsubmission(inspire_id=inspire_id)
            assert hep_submission is not None
            update_record(hep_submission.publication_recid, different_inspire_record_information)

            # Then can check that the update works and that a further update is not required.
            assert update_record_info(inspire_id, send_email=True) == 'Success'
            assert update_record_info(inspire_id) == 'No update needed'  # check case where information already current

        unload_submission(submission.publication_recid)


def test_set_review_status(app, load_default_data):
    """Test we can set review status on one or all data records"""
    # Set the status of a default record to "todo" so we can modify table
    # review status
    hepsubmission = get_latest_hepsubmission(publication_recid=1)
    hepsubmission.overall_status = "todo"
    db.session.add(hepsubmission)
    db.session.commit()

    data_reviews = DataReview.query.filter_by(publication_recid=1).all()
    assert(len(data_reviews) == 0)

    # Get data records
    data_submissions = DataSubmission.query.filter_by(
        publication_recid=hepsubmission.publication_recid).order_by(
        DataSubmission.id.asc()).all()
    assert(len(data_submissions) == 14)

    # Log in
    user = User.query.first()

    # Try setting a single data submission to "attention"
    params = {
        'publication_recid': 1,
        'status': 'attention',
        'version': 1,
        'data_recid': data_submissions[1].id
    }

    with app.test_request_context('/data/review/status/', data=params):
        login_user(user)
        result = set_data_review_status()
        assert(result.json == {'recid': 1,
                               'data_id': data_submissions[1].id,
                               'status': 'attention'})
        data_reviews = DataReview.query.filter_by(publication_recid=1).all()
        assert(len(data_reviews) == 1)
        assert(data_reviews[0].publication_recid == 1)
        assert(data_reviews[0].data_recid == data_submissions[1].id)
        assert(data_reviews[0].status == 'attention')

    # Now try setting all data submissions to "passed"
    params = {
        'publication_recid': 1,
        'status': 'passed',
        'version': 1,
        'all_tables': True
    }

    with app.test_request_context('/data/review/status/', data=params):
        login_user(user)
        result = set_data_review_status()
        assert(result.json == {'recid': 1, 'success': True})
        data_reviews = DataReview.query.filter_by(publication_recid=1) \
            .order_by(DataReview.data_recid.asc()).all()
        assert(len(data_reviews) == 14)
        for i, data_review in enumerate(data_reviews):
            assert(data_review.publication_recid == 1)
            assert(data_review.data_recid == data_submissions[i].id)
            assert(data_review.status == 'passed')


def test_get_all_ids(app, load_default_data, identifiers):
    expected_record_ids = [1, 16]
    # Order is not guaranteed unless we use latest_first,
    # so sort the results before checking
    assert(get_all_ids() == expected_record_ids)

    # Check id_field works
    assert(get_all_ids(id_field='recid') == expected_record_ids)
    assert(get_all_ids(id_field='inspire_id')
           == [int(x["inspire_id"]) for x in identifiers])
    with pytest.raises(ValueError):
        get_all_ids(id_field='authors')

    # Check last_updated works
    # Default records were last updated on 2016-07-13 and 2013-12-17
    date_2013_1 = datetime.datetime(year=2013, month=12, day=16)
    assert(sorted(get_all_ids(last_updated=date_2013_1)) == expected_record_ids)
    date_2013_2 = datetime.datetime(year=2013, month=12, day=17)
    assert(sorted(get_all_ids(last_updated=date_2013_2)) == expected_record_ids)
    date_2013_3 = datetime.datetime(year=2013, month=12, day=18)
    assert(get_all_ids(last_updated=date_2013_3) == [1])
    date_2020 = datetime.datetime(year=2020, month=1, day=1)
    assert(get_all_ids(last_updated=date_2020) == [])

    # Check sort by latest works
    assert(get_all_ids(latest_first=True) == expected_record_ids)
    assert(get_all_ids(id_field='inspire_id', latest_first=True)
           == [int(x["inspire_id"]) for x in identifiers])


def test_create_new_version(app, load_default_data, identifiers, mocker):
    hepsubmission = get_latest_hepsubmission(publication_recid=1)
    assert hepsubmission.version == 1

    # Add an uploader
    uploader = SubmissionParticipant(
        publication_recid=1,
        email='test@hepdata.net',
        role='uploader',
        status='primary')
    db.session.add(uploader)
    db.session.commit()

    user = User.query.first()

    # Mock `send_cookie_email` method
    send_cookie_mock = mocker.patch('hepdata.modules.records.api.send_cookie_email')

    # Create new version of valid finished record
    result = create_new_version(1, user, uploader_message="Hello!")
    assert result.status_code == 200
    assert result.json == {'success': True, 'version': 2}

    # get_latest_hepsubmission should now return version 2
    hepsubmission = get_latest_hepsubmission(publication_recid=1)
    assert hepsubmission.version == 2
    assert hepsubmission.overall_status == 'todo'

    # Should have attempted to send uploader email
    send_cookie_mock.assert_called_with(
        uploader,
        get_record_by_id(1),
        message="Hello!",
        version=2
    )

    # Try creating a new version - should not work as status of most recent is 'todo'
    result, status_code = create_new_version(1, user)
    assert status_code == 400
    assert result.json == {
        'message': 'Rec id 1 is not finished so cannot create a new version'
    }
