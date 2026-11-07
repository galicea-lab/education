#
import json

from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
from requests import sessions

from .mls_model import (
  RevokeStudentCardRequestDto,
  StudentCardCreateOrderRequestDto,
  StudentCardCreateOrderResponseDto,
  StudentCardOrderQrCodesResponseDto,
  StudentCardOrderStatusResponseDto,
  StudentCardOrderStudentStatusResponseDto,
)


def load_pkcs12_certificate(pfx_file : str, password :str):
  with open(pfx_file, "rb") as cert_file:
    cert_content = cert_file.read()
    try:
        bpassword = password.encode('utf-8')
        private_key, cert, additional_certs = pkcs12.load_key_and_certificates(
            cert_content, bpassword,
            # backend=default_backend()
        )
    except ValueError as ex:
        raise ValueError("Failed to deserialize certificate in PEM or PKCS12 format") from ex
    if not private_key:
        raise ValueError("The certificate must include its private key")
    if not cert:
        raise ValueError("Failed to deserialize certificate in PEM or PKCS12 format")
    key_bytes = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pem_sections = [key_bytes] + [c.public_bytes(Encoding.PEM) for c in [cert, *additional_certs]]
    pem_bytes = b"".join(pem_sections)
    #fingerprint = cert.fingerprint(hashes.SHA1())  # nosec

    return (pem_bytes, key_bytes)


class MlsAPI:

    def __init__(self,
                 host : str,
                 p12_path: str,
                 password: str):
      self.host=host
      self.access_token = ''
      self.password = password
      self.certificate_fn='mls_certificate.pem'
      self.private_key_fn='mls_key.pem'
      try:
        (self.pem_certificate,self.pem_private_key)=load_pkcs12_certificate(p12_path,password)
        fcert=open(self.certificate_fn,'w')
        cert=self.pem_certificate.decode('utf-8')
        s=cert.find('-----BEGIN CERT')
        cert[:s]
        scert=cert[s:]
        fcert.write(scert)
        fcert.close()
        fkey=open(self.private_key_fn,'w')
        fkey.write(self.pem_private_key.decode('utf-8')) # decrypted: spriv
        fkey.close()
      except Exception as e:
        print(e)
      self.cert=(self.certificate_fn, self.private_key_fn, self.password) # private key is crypted


    def request(self,method, url,
                params=None,
                data=None,
                headers=None):
      with sessions.Session() as session:
          session.cert=self.cert
          return session.request(method=method, url=url, headers=headers, params=params, data=data)



    def TokenRequest(self, API_CLIENT_ID, API_CLIENT_SECRET):
      dheaders = {
          'Content-Type': 'application/x-www-form-urlencoded',
      }
      token_url = '/realms/student-card/protocol/openid-connect/token'
      form_params = {
        'client_id': API_CLIENT_ID,
        'client_secret': API_CLIENT_SECRET,
        'grant_type': 'client_credentials'
      }
      try:
          host1=self.host
          result=self.request('POST',host1+token_url,data=form_params,headers=dheaders)
          if result.status_code==200:
              print('token pobrany?')
              print(result.content.decode())
              jresult=json.loads(result.content.decode())
              self.access_token=jresult['access_token']
              return self.access_token
      except Exception as e:
          print('Błąd: token nie pobrany')
          print(e)
          return ''
      return result

    def CreateOrder(self,
                    order : StudentCardCreateOrderRequestDto
                   ) -> StudentCardCreateOrderResponseDto:
        url='/school/external-service/api/student-cards/order'
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + self.access_token
        }
        try:
            data=order.model_dump_json()
            result = self.request('POST', self.host + url, data=data, headers=headers)
            if result.status_code == 200:
                try:
                    jresult = json.loads(result.content.decode())
                    return jresult
                except Exception as e:
                    print(e)
                    return None # StudentCardCreateOrderResponseDto(orderId=-1, students=[])
            else:
                try:
                    print(result.status_code)
                    print(result.reason)
                    print(result) #json.dumps(result.__dict__))
                    return None #StudentCardCreateOrderResponseDto(orderId=-2, students=[])
                except Exception as e:
                    print(e)
                return None # StudentCardCreateOrderResponseDto(orderId=-3, students=[])
        except Exception as e:
            print(e)
            return None # StudentCardCreateOrderResponseDto(orderId=-4, students=[])

    def GetOrderStatus(self,
                    order_id : str
                   ) -> StudentCardOrderStatusResponseDto:
        url='/school/external-service/api/student-cards/order/%s/status' % order_id
        headers = {
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + self.access_token
        }
        try:
            result = self.request('GET', self.host + url, headers=headers)
            if result.status_code == 200:
                try:
                    jresult = json.loads(result.content.decode())
                    return jresult
                except Exception as e:
                    print(e)
                    return None
            else:
                print(result.status_code)
                print(result)
                return None
        except Exception as e:
            print(e)
            return None

    def GetQrCodes(self,
                    order_id : str
                   ) -> StudentCardOrderQrCodesResponseDto:
        url='/school/external-service/api/student-cards/order/%s/qr-codes' % order_id
        headers = {
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + self.access_token
        }
        try:
            result = self.request('GET', self.host + url, headers=headers)
            if result.status_code == 200:
                try:
                    jresult = json.loads(result.content.decode())
                    return jresult
                except Exception as e:
                    print(e)
                    return None
            else:
                print(result.status_code)
                return None
        except Exception as e:
            print(e)
            return None

    def GetStudentStatus(self,
                         order_id : str, student_card_id : str) -> StudentCardOrderStudentStatusResponseDto:
        url='/school/external-service/api/student-cards/order/%s/students/%s/status' % (order_id, student_card_id)
        headers = {
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + self.access_token
        }
        try:
            result = self.request('GET', self.host + url, headers=headers)
            if result.status_code == 200:
                try:
                    jresult = json.loads(result.content.decode())
                    return jresult
                except Exception as e:
                    print(e)
                    return None
            else:
                print(result.status_code)
                return None
        except Exception as e:
            print(e)
            return None

    def RevokeStudentCard(self, card : RevokeStudentCardRequestDto):
        url='/school/external-service/api/student-cards/order'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + self.access_token
        }
        try:
            data=card.model_dump_json()
            result = self.request('POST', self.host + url, data=data, headers=headers)
            if result.status_code == 200:
                try:
                    return result.content.decode()
                except Exception as e:
                    print(e)
                    return None
            else:
                print(result.status_code)
                return None
        except Exception as e:
            print(e)
            return None

