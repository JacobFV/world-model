"""Institutional vocabulary. Declarations imply neither acquired evidence nor calibration."""

def schema():
    parents={
        'conflict':'entity','war':'conflict','military_unit':'organization','military_alliance':'organization',
        'academic_institution':'institution','researcher':'person','publication':'entity','research_paper':'publication',
        'research_project':'entity','exchange':'institution','ticker_listing':'entity','bond':'security','equity_security':'security',
        'derivative':'security','investment_position':'asset','online_platform':'organization','publisher':'organization',
        'post':'publication','statute':'law','regulation':'law','legal_case':'entity','ruling':'entity','court':'institution',
        'territory':'location','water_body':'location','ocean':'water_body','sea':'water_body','lake':'water_body',
        'river':'water_body','watershed':'location','land_cover':'entity','geographic_boundary':'entity','territorial_claim':'entity'}
    relations={name:{'domain':domain,'range':target} for name,domain,target in [
        ('participates_in_conflict','agent','conflict'),('conflict_in','conflict','location'),
        ('commands','agent','military_unit'),('allied_with','agent','agent'),
        ('alliance_member','agent','military_alliance'),('research_affiliation','researcher','academic_institution'),
        ('authored','person','publication'),('cites','publication','publication'),
        ('researches','agent','research_project'),('research_output','research_project','publication'),
        ('listed_instrument','ticker_listing','security'),('listed_on','ticker_listing','exchange'),
        ('issued_by','security','agent'),('position_in','investment_position','security'),
        ('published_by','publication',['agent','publisher']),('posted_on','post','online_platform'),
        ('replies_to','post','post'),('enacted_by','law','agent'),('applies_in','law','jurisdiction'),
        ('amends','law','law'),('heard_by','legal_case','court'),('case_party','legal_case','agent'),
        ('ruling_in','ruling','legal_case'),('interprets','ruling','law'),('overrules','ruling','ruling'),
        ('claimant','territorial_claim','agent'),('claimed_territory','territorial_claim','territory'),
        ('disputes_claim','agent','territorial_claim'),('boundary_of','geographic_boundary','location'),
        ('drains_to','watershed',['water_body','watershed']),('has_land_cover','location','land_cover'),
        ('borders','location','location'),('successor_of','agent','agent')]}
    variables={
        'lifecycle_size':{'type':'number','unit':'count','domain':'agent'},
        'lifecycle_status':{'type':'string','unit':'category','domain':'agent'},
        'ticker_symbol':{'type':'string','unit':'category','domain':'ticker_listing'},
        'listing_currency':{'type':'string','unit':'category','domain':'ticker_listing'},
        'publication_date':{'type':'string','unit':'ISO_date','domain':'publication'},
        'legal_effective_date':{'type':'string','unit':'ISO_date','domain':'law'},
        'territorial_claim_status':{'type':'string','unit':'category','domain':'territorial_claim'},
        'land_cover_fraction':{'type':'number','unit':'fraction','domain':'location'}}
    return {'entity_types':{k:{'parent':v} for k,v in parents.items()},'relations':relations,'variables':variables}
