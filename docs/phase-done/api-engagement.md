Di server pakai bash, bukan PowerShell. Gunakan ini:


TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo $TOKEN
Lalu test:


# Summary Global
curl -s "http://187.77.125.10:8000/api/v1/metrics/summary?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# SOV
curl -s "http://187.77.125.10:8000/api/v1/metrics/sov?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Trend per minggu
curl -s "http://187.77.125.10:8000/api/v1/metrics/trend?platforms=youtube&granularity=week" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

  cara test
  TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo $TOKEN


Di server pakai bash, bukan PowerShell. Gunakan ini:


TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo $TOKEN
Lalu test:


# Summary Global
curl -s "http://187.77.125.10:8000/api/v1/metrics/summary?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# SOV
curl -s "http://187.77.125.10:8000/api/v1/metrics/sov?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Trend per minggu
curl -s "http://187.77.125.10:8000/api/v1/metrics/trend?platforms=youtube&granularity=week" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool



  PowerShell. Gunakan ini:


TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo $TOKEN
Lalu test:


# Summary Global
curl -s "http://187.77.125.10:8000/api/v1/metrics/summary?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# SOV
curl -s "http://187.77.125.10:8000/api/v1/metrics/sov?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Trend per minggu
curl -s "http://187.77.125.10:8000/api/v1/metrics/trend?platforms=youtube&granularity=week" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool