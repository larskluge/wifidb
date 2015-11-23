package main

import (
	"log"

	"github.com/PuerkitoBio/goquery"
)

func Isp() (isp string) {
	doc, err := goquery.NewDocument("http://www.whoismyisp.org")
	if err != nil {
		log.Fatal(err)
	}

	doc.Find("h1").Each(func(i int, s *goquery.Selection) {
		isp = s.Text()
	})
	return
}
