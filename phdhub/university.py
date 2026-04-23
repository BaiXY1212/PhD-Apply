"""University source and ranking helpers."""

import re

import requests

QS_2025_TOP_100 = [
    "Massachusetts Institute of Technology", "Imperial College London", "University of Oxford", "Harvard University",
    "University of Cambridge", "Stanford University", "ETH Zurich", "National University of Singapore", "University College London",
    "California Institute of Technology", "University of Pennsylvania", "University of California, Berkeley", "University of Melbourne",
    "Peking University", "Nanyang Technological University", "Cornell University", "University of Hong Kong", "University of Sydney",
    "University of New South Wales", "Tsinghua University", "University of Chicago", "Princeton University", "Yale University",
    "PSL", "Ecole Polytechnique Federale de Lausanne", "Johns Hopkins University", "University of Edinburgh", "Technical University of Munich",
    "McGill University", "Australian National University", "Columbia University", "University of Tokyo", "University of California, Los Angeles",
    "University of Manchester", "King's College London", "Chinese University of Hong Kong", "New York University", "Fudan University",
    "Shanghai Jiao Tong University", "King Abdulaziz University", "Seoul National University", "Zhejiang University", "Monash University",
    "University of Queensland", "London School of Economics", "Kyoto University", "Hong Kong University of Science and Technology",
    "Delft University of Technology", "Northwestern University", "University of Amsterdam", "University of Bristol", "KAIST",
    "Sorbonne University", "Duke University", "University of Texas at Austin", "Ludwig-Maximilians-Universität", "Hong Kong Polytechnic University",
    "KU Leuven", "University of California, San Diego", "Universiti Malaya", "University of Washington", "University of Warwick",
    "City University of Hong Kong", "University of Illinois", "University of Auckland", "National Taiwan University", "Universidad de Buenos Aires",
    "University of St Andrews", "University of Birmingham", "Yonsei University", "Tohoku University", "Osaka University",
    "Trinity College Dublin", "Korea University", "University of Leeds", "University of Glasgow", "University of Western Australia",
    "University of Southampton", "Brown University", "Penn State University", "Lund University", "University of Adelaide",
    "KTH Royal Institute of Technology", "University of Sheffield", "Uppsala University", "University of Copenhagen", "Purdue University",
    "Boston University", "University of Nottingham", "Washington University in St. Louis", "University of Sao Paulo", "University of Helsinki",
    "RMIT", "Aarhus University", "University of Geneva", "University of Oslo", "Georgia Institute of Technology", "University of Zurich"
]

def get_qs_rank(name):
    name_lower = name.lower().replace("the ", "").strip()
    
    # 1. 完全一致
    for i, qs_name in enumerate(QS_2025_TOP_100):
        qs_lower = qs_name.lower().strip()
        if qs_lower == name_lower:
            return i
            
    # 2. 词边界精确匹配 (防止 psl 匹配到 upslal, 避免误伤)
    for i, qs_name in enumerate(QS_2025_TOP_100):
        qs_lower = qs_name.lower().strip()
        
        # 处理别名
        if qs_lower == "eth zurich" and ("eth z" in name_lower or "eidgenössische technische hochschule" in name_lower):
            return i
        if qs_lower == "ecole polytechnique federale de lausanne" and ("epfl" in name_lower or "fédérale de lausanne" in name_lower):
            return i
        if qs_lower == "ludwig-maximilians-universität" and "ludwig-maximilian" in name_lower:
            return i
        if qs_lower == "technical university of munich" and ("technische universität münchen" in name_lower or "tum " in name_lower.replace("-", " ")):
            return i
        if qs_lower == "kth royal institute of technology" and ("kungliga tekniska" in name_lower or "kth" in name_lower.split()):
            return i
        if qs_lower == "kaist" and "korea advanced institute of science" in name_lower:
            return i
        if qs_lower == "psl" and ("psl research" in name_lower or "paris-sciences-et-lettres" in name_lower):
            return i
        if qs_lower == "postech" and "pohang university" in name_lower:
            return i
        if qs_lower == "universiti malaya" and "university of malaya" in name_lower:
            return i
            
        import re
        if re.search(r'\b' + re.escape(qs_lower) + r'\b', name_lower):
            # 防止城市大学等包含港大
            if qs_lower == "university of hong kong" and not name_lower.startswith("university of hong kong"):
                continue
            if qs_lower == "university of washington" and "st. louis" in name_lower:
                continue
            if qs_lower == "washington university" and "st. louis" not in name_lower:
                continue
            if qs_lower == "university of york" and "new york" in name_lower:
                continue
                
            return i
            
    return 9999


def get_world_universities():
    try:
        url = "https://raw.githubusercontent.com/Hipo/university-domains-list/master/world_universities_and_domains.json"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        
        country_code_map = {}
        for item in data:
            c = item.get("country")
            code = item.get("alpha_two_code")
            if c and code and len(code) == 2 and c not in country_code_map:
                base = 127397
                u_code = code.upper()
                if u_code == 'UK': u_code = 'GB'
                flag = chr(ord(u_code[0]) + base) + chr(ord(u_code[1]) + base)
                country_code_map[c] = flag
                
        country_univ_map = {}
        for item in data:
            c = item.get("country")
            name = item.get("name")
            if c and name:
                # 仅保留在 QS 名单内的学校
                if get_qs_rank(name) < 9999:
                    flag = country_code_map.get(c, "🏳️")
                    c_key = f"{flag} {c}"
                    if c_key not in country_univ_map:
                        country_univ_map[c_key] = []
                    country_univ_map[c_key].append(name)
                
        for c in country_univ_map:
            sorted_list = sorted(list(set(country_univ_map[c])), key=lambda x: (get_qs_rank(x), x))
            formatted_list = []
            for x in sorted_list:
                rank = get_qs_rank(x)
                if rank < 9999:
                    formatted_list.append(f"{x} (QS 2025 #{rank + 1})")
                else:
                    formatted_list.append(x)
            country_univ_map[c] = formatted_list
            
        return country_univ_map
    except Exception as e:
        return {"🇺🇸 United States": ["MIT", "Stanford"], "🇬🇧 United Kingdom": ["Cambridge"], "🇨🇳 China": ["Tsinghua"]}
