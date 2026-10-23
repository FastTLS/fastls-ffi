from fastls import Session

with Session(profile="chrome/156/windows") as session:
    response = session.get("https://tls.peet.ws/api/all")
    print(response.status_code)
    print(response.text)
    print(response.match)
