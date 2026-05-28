# DocuMind 설치 패키지

DocuMind를 단일 AWS EC2 서버에 배포하기 위한 최소 설치 패키지입니다.

구성은 단순하게 유지했습니다.

- `compose/`: API와 Web 컨테이너 실행 파일
- `images/`: API와 Web Dockerfile
- `ec2/terraform/single/`: 단일 EC2 배포 Terraform

## 인프라 선택

DocuMind는 환경 변수로 저장소와 데이터베이스를 선택합니다.

| 용도 | 기본값 | 대체값 |
| --- | --- | --- |
| Database | `DATABASE_TYPE=sqlite` | `DATABASE_TYPE=postgresql` |
| File storage | `STORAGE_TYPE=local` | `STORAGE_TYPE=s3` |

Terraform에서도 같은 선택을 사용합니다.

- `database_type = "sqlite"`: EC2 로컬 디스크에 SQLite DB 생성
- `database_type = "postgresql"`: RDS PostgreSQL 생성 후 API 환경 변수에 연결 정보 주입
- `storage_type = "local"`: EC2 로컬 볼륨의 `/var/lib/documind/outputs` 사용
- `storage_type = "s3"`: S3 버킷 생성 후 API 환경 변수에 버킷 정보 주입

## 빠른 시작

```bash
cd setup/ec2/terraform/single
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform apply
```

배포가 끝나면 Terraform output의 `web_url`, `api_url`로 접속합니다.

## 운영 메모

- 기본 LLM provider는 `bedrock`이며, EC2 Instance Profile에 Bedrock 호출 권한을 붙일 수 있습니다.
- API 키 기반 provider를 쓰는 경우 `terraform.tfvars`에 해당 키를 입력합니다. `terraform.tfstate`에 민감 정보가 저장될 수 있으니 원격 backend와 접근 제어를 사용하세요.
- SSH key를 지정하지 않으면 SSM Session Manager로 접속하는 구성을 권장합니다.
- `.env`는 EC2의 `/opt/documind/deploy/.env`에 생성되며 권한은 `600`입니다.

## Destroy / 인프라 삭제 가이드

환경이 더 이상 필요 없으면 Terraform이 생성한 리소스를 삭제합니다.

### 방법 A) 헬퍼 스크립트 사용

**Linux / macOS**

```bash
cd setup/ec2/terraform/single
./tf-delete.sh
```

**Windows (PowerShell)**

```powershell
cd setup/ec2/terraform/single
.\tf-delete.ps1
```

### 방법 B) Terraform 직접 실행

```bash
cd setup/ec2/terraform/single
terraform destroy
```

### 삭제 전 체크리스트

- 현재 Terraform workspace/state가 삭제 대상 환경인지 확인합니다.
- 필요한 데이터(DB, 생성 문서, 로그)는 먼저 백업합니다.
- `terraform.tfvars`에 API 키를 넣었다면 삭제 후 키를 회수/재발급합니다.
- 삭제 후 `terraform state list`로 잔여 리소스가 없는지 확인합니다.

