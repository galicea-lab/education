# https://polon2.opi.org.pl/fields-of-study-api/swagger-ui/index.html#/Studenci/addStudent_1

import json
import uuid
from datetime import datetime

import six
from requests import sessions

from education.integrations.config import section_config

_polon_config = section_config('polon')
from .student_models import (
  AcademicSemester,
  AccumulatedEcts,
  AccumulatedEctsTeacherTraining,
  ConfirmedLearningOutcomesEcts,
  CorrectSemesterWithoutFosRequest,
  # StudentRequest
  CourseAssignedToFieldOfStudyRequest,
  Gender,
  GeneralInformationRequest,
  IdentificationDataRequest,
  IdentificationDocumentDataRequest,
  ImportCourseAssignedToFieldOfStudyRequest,
  ImportGeneralInformationRequest,
  ImportPersonRequest,
  ImportSemesterWithFosRequest,
  ImportStudentCourseDataRequest,
  ImportStudentInUniversityRequest,
  Pageable,
  PagedModelEntityModelStudentInUniversityBasicResponseDocument,
  PlaceOfResidence,
  SemesterWithFosRequest,
  StudentCourseDataRequest,
  StudentPersonalDataRequest,
  StudentRequest,
  StudentSearchFilter,
)


def dmerge(a,b):
  keys=a.keys()|b.keys()
  result={}
  for k in keys:
    if k in a:
      if a[k] or a[k]==0:
        result[k]=a[k]
    else:
      if b[k] or b[k]==0:
        result[k]=b[k]
  return result


class PolonAPI:

  def __init__(self, INSTITUTION, API_URL="https://polon2.opi.org.pl"):
    self.API_URL = API_URL
    self.INSTITUTION=INSTITUTION
    self.access_token = None

  def request(self,method, url,
              params=None,
              data=None,
              headers=None):
    with sessions.Session() as session:
        return session.request(method=method, url=url, headers=headers, params=params, data=data)

  def post_auth_request(self,username,password):
    """
    Zapytanie:
    curl -X POST "https://mcl.opi.org.pl/auth/realms/OPI/protocol/openid-connect/token" \
   --header "Content-Type: application/x-www-form-urlencoded" \
   --data-urlencode "client_id=polon2" \
   --data-urlencode 'grant_type=password' \
   --data-urlencode "username=$USER" \
   --data-urlencode "password=$PASS"`

    """
    token_url='https://mcl.opi.org.pl/auth/realms/OPI/protocol/openid-connect/token'
    header_params={
     'Content-Type': 'application/x-www-form-urlencoded',
     'Accept': '*/*',
    }
    form_params={
      'client_id':'polon2',
      'grant_type':'password',
      'username': username,
      'password':password
    }
    try:
      result = self.request('POST', token_url, data=form_params, headers=header_params)
      if result.status_code == 200:
        jresult = json.loads(result.content.decode())
        self.access_token = jresult['access_token']
        return self.access_token
    except Exception as e:
      print(e)
      return ''


  def GetFos(self):
      url=self.API_URL+'/fields-of-study-api/v3.0/fos?page=0&size=25&sort=name%2Casc'
      headers = {
          'Accept': 'application/json,text/plain,*/*',
          'Authorization': 'Bearer ' + self.access_token,
          'institution': self.INSTITUTION
        }
      try:
          result = self.request('GET', url, headers=headers)
          if result.status_code == 200:
              try:
                  jresult = json.loads(result.content.decode())
                  yield from jresult["_embedded"]["fosDocumentList"]
              except Exception as e:
                  print(e)
          else:
              print(result.status_code)
      except Exception as e:
          print(e)

  def FindStudents(self,
    filter: StudentSearchFilter,
    pageable: Pageable = ...
  ) -> dict:
   #EntityModelStudentInUniversityBasicResponseDocument:
    #PagedModelEntityModelStudentInUniversityBasicResponseDocument:
    """
    przykład:
    pageable={
      page:0,size=25,sort=surname%2CASC
    }
    Wyszukiwanie studentów na uczelni
    curl -X GET  "$API_URL/fields-of-study-api/v3.0/university/students?page=0&size=25&sort=surname%2CASC&institutionId=a615aecb-b546-472b-93d9-813564c4b50d" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "institution: $INSTITUTION"\
  -H "accept: application/json,text/plain,*/*"
    """
    url = self.API_URL + '/fields-of-study-api/v3.0/university/students'
    headers = {
      'Accept': 'application/json,text/plain,*/*',
      'Authorization': 'Bearer ' + self.access_token,
      'institution': self.INSTITUTION
    }
    try:
      params=dmerge(filter.dict(),pageable.dict())
      if 'sort' in params:
        params['sort']=','.join(params['sort'])
      for k in params:
        if not isinstance(params[k], str):
          params[k]=str(params[k]).split('.')[-1]
      params['institutionId']=self.INSTITUTION
      result = self.request('GET', url, headers=headers, params=params)
      if result.status_code == 200:
        try:
          jresult = json.loads(result.content.decode())
          yield from jresult["_embedded"]["studentInUniversityBasicResponseDocumentList"]
        except Exception as e:
          print(e)
      else:
        print(result.status_code)
    except Exception as e:
      print(e)

  ###  nowy semestr #################################

  def add_semester_on_study_without_fos(self, student_in_university_id, study_id, semestr):
    """Dodanie nowego semestru bez przypisania do kierunku  # noqa: E501
    """
    headers = {
      'Accept': 'application/json,text/plain,*/*',
      'Authorization': 'Bearer ' + self.access_token,
      'institution': self.INSTITUTION
    }
    body_params = CorrectSemesterWithoutFosRequest(
      studySemester= semestr
    )
    body=body_params.json()
    url=f'/university/students/{student_in_university_id}/studies/{study_id}/semesters/without-fos-instance'
    self.request('POST', url, headers=headers, data=body)

  ######################################

  ###### nowe

  def add_student1(self, student): # : StudentRequest):
    print(student.json())
    url = self.API_URL + '/fields-of-study-api/v1.0/university/students'
    #url = self.API_URL + '/fields-of-study-api/university/students'
    headers = {
      'Accept': 'application/json,text/plain,*/*',
      'Authorization': 'Bearer ' + self.access_token,
      'institution': self.INSTITUTION,
      'Content-Type':'application/json',

    }
    try:
      body_params = student.json()
      result = self.request('POST', url, headers=headers, data=body_params)
      if (result.status_code == 405) or (result.status_code == 403):
        print('Brak uprawnień lub błędny endpoint')
        print(result.content)
        return result.content
      if result.status_code == 400:
        print('Nieudane dodwanie')
        print(result.content)
        return result.content
      if result.status_code == 200:
        return result.content
    except Exception as e:
      print(e)

  def add_student1_with_http_info_org(self, student : ImportStudentInUniversityRequest):
      # Dodawanie nowego studenta
      all_params = ['body']
      all_params.append('async_req')
      all_params.append('_return_http_data_only')
      all_params.append('_preload_content')
      all_params.append('_request_timeout')

      collection_formats = {}
      path_params = {}
      query_params = []
      header_params = {}
      form_params = []
      local_var_files = {}
      body_params = student.json()
      # HTTP header `Accept`
      header_params['Accept'] = self.api_client.select_header_accept(
          ['application/json','text/plain','*/*'])
      # HTTP header `Content-Type`
      header_params['Content-Type'] = self.api_client.select_header_content_type(
          ['application/json'])
      # Authentication setting
      header_params['Authorization'] = 'Bearer ' + self.access_token
      header_params['institution']=self.INSTITUTION

      auth_settings = ['institution', 'mcl']

      return self.api_client.call_api(
          '/v1.0/university/students', 'POST',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type='str',
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=False,
          _preload_content=True,
          _request_timeout=300,
          collection_formats=collection_formats)

  def upd_student_semestr(self, studentId, semestrId):
      collection_formats = {}
      path_params = {}
      query_params = []
      header_params = {}
      form_params = []
      local_var_files = {}
      body_params = {}
      header_params['Accept'] = self.api_client.select_header_accept(
          ['application/json','text/plain','*/*'])
      header_params['Content-Type'] = self.api_client.select_header_content_type(
          ['application/json'])
      header_params['Authorization'] = 'Bearer ' + self.access_token
      header_params['institution']=self.INSTITUTION
      auth_settings = ['institution', 'mcl']
      return self.api_client.call_api(
          '/v1.0/university/students', 'POST',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type='str',
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=False,
          _preload_content=True,
          _request_timeout=300,
          collection_formats=collection_formats)

def polon_add(nr_albumu, nazwisko, imie_i, imie_ii,
    rok_urodz=2001,
    pesel='',
    nr_do='AZ99999',
    ects=30,
    kierunek='Projektowanie graficzne',
    poziom='II',
    polon=None):

    ects_razem=ects

    polon_username = _polon_config.get('polon_user')
    polon_password = _polon_config.get('polon_password')
    polon_institution = _polon_config.get('polon_institution')
    foss=[]
    if not polon:
      try:
        polon = PolonAPI(polon_institution, "https://polon2.opi.org.pl")
        polon.post_auth_request(polon_username, polon_password)
      except Exception as e:
        print(e)
    try:
      for f in polon.GetFos():
        foss.append(f)
    except Exception as e:
      print(e)
    fos=None
    for f in foss:
        if f['name']==kierunek and f['level']=='LEVEL_'+poziom:
            fos=f['fosCode']
    if not fos:
      print('Nieznany kierunek %s - %s' % (kierunek, poziom))
    else:
      stud = StudentRequest(
        studentPersonalData=StudentPersonalDataRequest(
          name = imie_i,
          otherNames = imie_ii,
          surnamePrefix = '',
          surname = nazwisko,
          gender = Gender.MALE,
          birthYear = rok_urodz,
          citizenships = ["PL"],
          birthCountry = "PL",
          originCountry = "PL",
          hasPlCard=False,
          identificationData=IdentificationDataRequest(
            pesel=pesel,
            document=None if not nr_do else IdentificationDocumentDataRequest(
              documentCountry='PL',
              documentNumber=nr_do,
              documentType='ID_CARD'
            )
          ),
        ),
        studentCourseData=StudentCourseDataRequest(
          generalInformation=GeneralInformationRequest(
            educationStartDate='2024-10-01',
            discontinuationDate=None,
            diplomaData=None,
            placeOfResidence=PlaceOfResidence.CITY,
            note='',
            exclusionFromStudiesProcedure=False,
            teacherTraining=False,
            #coLedStudy=False,
            basesForAdmission=[],
            basesForExemptionFromFees=[],
            financialAids=[]
          ),
          courseAssignedToFieldOfStudy=CourseAssignedToFieldOfStudyRequest(
            #interfacultyFosId=None,  # '00000009-0009-0009-0009-000000000009',
            semesters=[SemesterWithFosRequest(
              academicYear='2024/2025',
              academicSemester=AcademicSemester.WINTER,
              studySemester=1,
              accumulatedEcts=ects_razem,
              accumulatedEctsTeacherTraining=0,
              confirmedLearningOutcomesEcts=0,
              fieldOfStudyInstanceCode=fos,
              fieldOfStudyInstanceCodeEmpty=False
              , )]
          )
        )
      )
      polon.add_student1(stud)


def polon_zmiana_semestru(StudentId, SemestrId,
              polon=None):
  polon_username = _polon_config.get('polon_user')
  polon_password = _polon_config.get('polon_password')
  polon_institution = _polon_config.get('polon_institution')
  if not polon:
    try:
      polon = PolonAPI(polon_institution, "https://polon2.opi.org.pl")
      polon.post_auth_request(polon_username, polon_password)
    except Exception as e:
      print(e)
  polon.add_student1('')


def openPolon():
  polon_username = _polon_config.get('polon_user')
  polon_password = _polon_config.get('polon_password')
  polon_institution = _polon_config.get('polon_institution')
  try:
    polon = PolonAPI(polon_institution, "https://polon2.opi.org.pl")
    polon.post_auth_request(polon_username, polon_password)
    return polon
  except Exception as e:
    print(e)
    return None

def polon_fos(kierunek, poziom, polon=None):
  if not polon:
    polon_username = _polon_config.get('polon_user')
    polon_password = _polon_config.get('polon_password')
    polon_institution = _polon_config.get('polon_institution')
    try:
      polon = PolonAPI(polon_institution, "https://polon2.opi.org.pl")
      polon.post_auth_request(polon_username, polon_password)
    except Exception as e:
      print(e)
      return None
  try:
    #polon.add_student1test(1111)
    fos=None
    for f in polon.GetFos():
      if f['name'] == kierunek and f['level'] == 'LEVEL_' + poziom:
        fos = f['fosCode']
    if not fos:
      print('Nieznany kierunek %s - %s' % (kierunek, poziom))
      return None
  except Exception as e:
    print(e)
    return None
  return fos

def polon_add_stud(stud : ImportStudentInUniversityRequest, polon=None):
  if not polon:
    polon_username = _polon_config.get('polon_user')
    polon_password = _polon_config.get('polon_password')
    polon_institution = _polon_config.get('polon_institution')
    try:
      polon = PolonAPI(polon_institution, "https://polon2.opi.org.pl")
      polon.post_auth_request(polon_username, polon_password)
    except Exception as e:
      print(e)
  polon.add_student1(student=stud)


