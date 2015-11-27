package main

import (
	"encoding/json"
	"log"
	"net/http"
)

type Response struct {
	Status      string
	City        string // seems unprecise
	Country     string
	CountryCode string
	Isp         string
	// Timezone string // e.g. Asia/Ho_Chi_Minh
}

func getJson(url string, target interface{}) error {
	r, err := http.Get(url)
	if err != nil {
		return err
	}
	defer r.Body.Close()

	return json.NewDecoder(r.Body).Decode(target)
}

func Location(data *Info) {
	resp := Response{}
	err := getJson("http://ip-api.com/json", &resp)
	if err == nil && resp.Status == "success" {
		data.Isp = resp.Isp
		data.City = resp.City
		data.Country = resp.Country
		data.CountryCode = resp.CountryCode
	} else {
		log.Printf("Could not detect location: %v", err)
	}
}
